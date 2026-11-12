#!/usr/bin/env python3
"""TK-383: базис по сделкам θ=50 (data/tk383/trades.csv): 1ч свечи close двух бирж, PnL базиса = b0-b1 (бп), MAE = max(b_t-b0).
b = ln(Pшорт/Pлонг)*1e4. Выход docs/findings/tk383-basis-2026-10-24.json + stdout."""
import json, sys, time
from concurrent.futures import ThreadPoolExecutor
import numpy as np, pandas as pd, requests

D = "data/tk383"
S = requests.Session()
L = json.load(open(f"{D}/lists.json"))
f = pd.read_csv(f"{D}/funding.csv", usecols=["ex", "coin", "sym"]).drop_duplicates()
START = int(pd.Timestamp("2026-09-01", tz="UTC").timestamp() * 1000)
END = int(time.time() * 1000)


def get(url, p=None):
    for i in range(4):
        try:
            r = S.get(url, params=p, timeout=20)
            if r.status_code == 200:
                return r.json()
            time.sleep(2 + i)
        except Exception:
            time.sleep(1 + i)


def kl(ex, sym):
    out = {}
    if ex == "bybit":
        end = END
        while True:
            j = get("https://api.bybit.com/v5/market/kline", dict(category="linear", symbol=sym, interval="60", start=START, end=end, limit=1000))
            l = j["result"]["list"] if j and j["retCode"] == 0 else []
            for x in l: out[int(x[0])] = float(x[4])
            if len(l) < 1000: break
            end = min(int(x[0]) for x in l) - 1
    elif ex == "binance":
        st = START
        while True:
            j = get("https://fapi.binance.com/fapi/v1/klines", dict(symbol=sym, interval="1h", startTime=st, limit=1500)) or []
            for x in j: out[int(x[0])] = float(x[4])
            if len(j) < 1500: break
            st = int(j[-1][0]) + 1
    elif ex == "gate":
        j = get("https://api.gateio.ws/api/v4/futures/usdt/candlesticks", dict(contract=sym, interval="1h", **{"from": START // 1000, "to": END // 1000})) or []
        for x in j: out[int(x["t"]) * 1000] = float(x["c"])
    elif ex == "bitget":
        st = START
        while True:
            j = get("https://api.bitget.com/api/v2/mix/market/history-candles", dict(symbol=sym, productType="usdt-futures", granularity="1H", startTime=st, endTime=min(st + 990 * 3600000, END), limit=200))
            l = j["data"] if j and j.get("code") == "00000" else []
            for x in l: out[int(x[0])] = float(x[4])
            st = st + 200 * 3600000 if len(l) >= 200 else min(st + 990 * 3600000, END)
            if st >= END: break
    elif ex == "bingx":
        j = get("https://open-api.bingx.com/openApi/swap/v3/quote/klines", dict(symbol=sym, interval="1h", startTime=START, endTime=END, limit=1440))
        for x in (j or {}).get("data", []) or []: out[int(x["time"])] = float(x["close"])
    return pd.Series(out).sort_index()


R = pd.read_csv(f"{D}/trades.csv", parse_dates=["open", "close"])
R = R[R.th == 50]
need = set()
for _, r in R.iterrows():
    need.add((r.a, r.coin)); need.add((r.b, r.coin))
sym = {(e, c): s for e, c, s in f.itertuples(index=False)}
need = [k for k in need if k in sym]
with ThreadPoolExecutor(16) as p:
    K = dict(zip(need, p.map(lambda k: kl(k[0], sym[k]), need)))
print("kline series:", len(K), "пусты:", sum(len(v) == 0 for v in K.values()))

rows = []
for r in R.itertuples():
    A, B = K.get((r.a, r.coin)), K.get((r.b, r.coin))
    if A is None or B is None or len(A) == 0 or len(B) == 0:
        continue
    # 1ч свечи с t в [open, close] включительно; цена на 00:00 open ~ close свечи 23:00 пред. дня -> берём свечу open-1ч
    t0 = int((r.open - pd.Timedelta(hours=1)).timestamp() * 1000); t1 = int((r.close - pd.Timedelta(hours=1)).timestamp() * 1000)
    ab = pd.concat([A, B], axis=1, keys=["a", "b"]).dropna()
    ab = ab[(ab.index >= t0) & (ab.index <= t1)]
    if len(ab) < 2: continue
    b = np.log(ab.a / ab.b) * 1e4 * r.pos  # pos=+1: шорт a/лонг b -> вред при росте b
    rows.append((r.coin, r.cls, r.a, r.b, r.open, r.close, r.gross, r.netT, b.iloc[0], b.iloc[-1], (b - b.iloc[0]).max(), (b - b.iloc[0]).min()))
X = pd.DataFrame(rows, columns=["coin", "cls", "a", "b", "open", "close", "gross", "netT", "b0", "b1", "up", "down"])
X.to_csv(f"{D}/basis-raw.csv", index=False)
print(len(X), "сделок с базисом из", len(R))
