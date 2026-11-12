#!/usr/bin/env python3
"""TK-383 (возврат Судьи): базис и MAE на 1м-свечах для сделок θ=50 (data/tk383/basis-raw.csv -> окна open..close).
Кэш свечей: data/tk383/k1m/<ex>_<sym>_<t0>_<t1>.pkl (возобновляемо). Выход data/tk383/basis1m-raw.csv.
b = ln(Pшорт/Pлонг)*1e4*pos; b0 = закрытие минуты open-1м, b1 = закрытие минуты close-1м; up = max(b_t-b0) по минутам."""
import json, os, sys, time, math
from concurrent.futures import ThreadPoolExecutor
import numpy as np, pandas as pd, requests

D = "data/tk383"; os.makedirs(f"{D}/k1m", exist_ok=True)
S = requests.Session(); S.mount("https://", requests.adapters.HTTPAdapter(pool_maxsize=64))
f = pd.read_csv(f"{D}/symmap.csv")
sym = {(e, c): s for e, c, s in f.itertuples(index=False)}
M = 60000


def get(url, p=None):
    for i in range(5):
        try:
            r = S.get(url, params=p, timeout=25)
            if r.status_code == 200: return r.json()
            time.sleep(1.5 * (i + 1))
        except Exception:
            time.sleep(1 + i)


def kl(ex, s, t0, t1):  # мс, минутные свечи [t0, t1) -> Series close по open-time
    fn = f"{D}/k1m/{ex}{'5' if ex == 'gate' else ''}_{s}_{t0}_{t1}.pkl"
    if os.path.exists(fn): return pd.read_pickle(fn)
    out = {}
    if ex == "bybit":
        end = t1 - 1
        while end >= t0:
            j = get("https://api.bybit.com/v5/market/kline", dict(category="linear", symbol=s, interval="1", start=t0, end=end, limit=1000))
            l = j["result"]["list"] if j and j["retCode"] == 0 else []
            for x in l: out[int(x[0])] = float(x[4])
            if len(l) < 1000: break
            end = min(int(x[0]) for x in l) - 1
    elif ex == "binance":
        st = t0
        while st < t1:
            j = get("https://fapi.binance.com/fapi/v1/klines", dict(symbol=s, interval="1m", startTime=st, endTime=t1 - 1, limit=1500)) or []
            for x in j: out[int(x[0])] = float(x[4])
            if len(j) < 1500: break
            st = int(j[-1][0]) + 1
    elif ex == "gate":
        st = t0
        while st < t1:
            # Gate отдаёт 1м только за последние 10000 точек (~7 сут) -> 5м; индекс = минута закрытия свечи (t+4м)
            en = min(st + 999 * 5 * M, t1 - M)
            j = get("https://api.gateio.ws/api/v4/futures/usdt/candlesticks", dict(contract=s, interval="5m", **{"from": st // 1000, "to": en // 1000})) or []
            for x in j: out[int(x["t"]) * 1000 + 4 * M] = float(x["c"])
            st = en + 5 * M
    elif ex == "bitget":
        st = t0
        while st < t1:
            en = min(st + 199 * M, t1 - M)
            j = get("https://api.bitget.com/api/v2/mix/market/history-candles", dict(symbol=s, productType="usdt-futures", granularity="1m", startTime=st, endTime=en, limit=200))
            for x in (j or {}).get("data", []) or []: out[int(x[0])] = float(x[4])
            st = en + M
    elif ex == "bingx":
        st = t0
        while st < t1:
            en = min(st + 1439 * M, t1 - M)
            j = get("https://open-api.bingx.com/openApi/swap/v3/quote/klines", dict(symbol=s, interval="1m", startTime=st, endTime=en, limit=1440))
            for x in (j or {}).get("data", []) or []: out[int(x["time"])] = float(x["close"])
            st = en + M
    ser = pd.Series(out, dtype=float).sort_index()
    ser = ser[(ser.index >= t0) & (ser.index < t1)]
    ser.to_pickle(fn)
    return ser


if __name__ == "__main__":
    R = pd.read_csv(f"{D}/basis-raw.csv", parse_dates=["open", "close"])
    lim = int(sys.argv[1]) if len(sys.argv) > 1 else len(R)
    R = R.iloc[:lim]
    # pos восстанавливаем из trades.csv
    T = pd.read_csv(f"{D}/trades.csv", parse_dates=["open", "close"]); T = T[T.th == 50][["coin", "a", "b", "open", "close", "pos"]]
    R = R.merge(T, on=["coin", "a", "b", "open", "close"], how="left")
    jobs = []
    for r in R.itertuples():
        t0 = int((r.open - pd.Timedelta(minutes=1)).timestamp() * 1000); t1 = int(r.close.timestamp() * 1000)
        for e in (r.a, r.b):
            if (e, r.coin) in sym: jobs.append((e, sym[(e, r.coin)], t0, t1))
    jobs = sorted(set(jobs))
    print("задач:", len(jobs), flush=True)
    t = time.time(); done = [0]
    def run(j):
        k = kl(*j); done[0] += 1
        if done[0] % 50 == 0: print(done[0], round(time.time() - t), "с", flush=True)
        return k
    with ThreadPoolExecutor(48) as p:
        res = dict(zip(jobs, p.map(run, jobs)))
    rows = []
    for r in R.itertuples():
        t0 = int((r.open - pd.Timedelta(minutes=1)).timestamp() * 1000); t1 = int(r.close.timestamp() * 1000)
        ka, kb = (r.a, sym.get((r.a, r.coin))), (r.b, sym.get((r.b, r.coin)))
        A = res.get((ka[0], ka[1], t0, t1)); B = res.get((kb[0], kb[1], t0, t1))
        if A is None or B is None or len(A) < 2 or len(B) < 2: continue
        ab = pd.concat([A, B], axis=1, keys=["a", "b"]).dropna()
        if len(ab) < 2: continue
        b = np.log(ab.a / ab.b) * 1e4 * r.pos
        n_exp = (t1 - t0) // M
        rows.append((r.coin, r.cls, r.a, r.b, r.open, r.close, r.gross, r.netT, b.iloc[0], b.iloc[-1], (b - b.iloc[0]).max(), (b - b.iloc[0]).min(), len(ab) / (n_exp / (5 if 'gate' in (r.a, r.b) else 1))))
    X = pd.DataFrame(rows, columns=["coin", "cls", "a", "b", "open", "close", "gross", "netT", "b0", "b1", "up", "down", "cover"])
    X.to_csv(f"{D}/basis1m-raw.csv", index=False)
    print(len(X), "сделок из", len(R), flush=True)
