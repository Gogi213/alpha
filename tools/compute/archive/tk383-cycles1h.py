#!/usr/bin/env python3
"""TK-383 (возврат v3): циклы gross>=1000 бп — базис на ВХОДЕ и ВЫХОДЕ по 1ч-свечам (глубина всех бирж, без дыр 1м/5м).
b = ln(Pшорт/Pлонг)*1e4*pos; вход = open 1ч-свечи open-дня (00:00 UTC), выход = close последней 1ч-свечи перед датой close.
Выход: data/tk383/cycles1h.csv, docs/findings/tk383-cycles1h-2026-10-24.txt"""
import importlib.util, json, sys
import numpy as np, pandas as pd
sp = importlib.util.spec_from_file_location("b1m", "tools/compute/tk383-basis1m.py"); b1m = importlib.util.module_from_spec(sp); sp.loader.exec_module(b1m)
D = "data/tk383"; H = 3600000; get = b1m.get
sym = b1m.sym; sym.update({("okx", c): s for c, s in {v: k for k, v in json.load(open(f"{D}/lists-okx.json")).items()}.items()})
cache = {}
def kl(ex, s, t0, t1):  # [t0,t1) мс -> DataFrame o,c по open-time
    k = (ex, s, t0, t1)
    if k in cache: return cache[k]
    o = {}
    if ex == "bybit":
        end = t1 - 1
        while end >= t0:
            l = ((get("https://api.bybit.com/v5/market/kline", dict(category="linear", symbol=s, interval="60", start=t0, end=end, limit=1000)) or {}).get("result") or {}).get("list") or []
            for x in l: o[int(x[0])] = (float(x[1]), float(x[4]))
            if len(l) < 1000: break
            end = min(int(x[0]) for x in l) - 1
    elif ex == "binance":
        for x in get("https://fapi.binance.com/fapi/v1/klines", dict(symbol=s, interval="1h", startTime=t0, endTime=t1 - 1, limit=1500)) or []: o[int(x[0])] = (float(x[1]), float(x[4]))
    elif ex == "gate":
        st = t0
        while st < t1:
            en = min(st + 999 * H, t1 - H)
            for x in get("https://api.gateio.ws/api/v4/futures/usdt/candlesticks", dict(contract=s, interval="1h", **{"from": st // 1000, "to": en // 1000})) or []: o[int(x["t"]) * 1000] = (float(x["o"]), float(x["c"]))
            st = en + H
    elif ex == "bitget":
        st = t0
        while st < t1:
            en = min(st + 199 * H, t1 - H)
            for x in (get("https://api.bitget.com/api/v2/mix/market/history-candles", dict(symbol=s, productType="usdt-futures", granularity="1H", startTime=st, endTime=en, limit=200)) or {}).get("data") or []: o[int(x[0])] = (float(x[1]), float(x[4]))
            st = en + H
    elif ex == "bingx":
        for x in (get("https://open-api.bingx.com/openApi/swap/v3/quote/klines", dict(symbol=s, interval="1h", startTime=t0, endTime=t1 - 1, limit=1000)) or {}).get("data") or []: o[int(x["time"])] = (float(x["open"]), float(x["close"]))
    elif ex == "okx":
        end = t1
        while end > t0:
            l = (get("https://www.okx.com/api/v5/market/history-candles", dict(instId=s, bar="1H", after=end, limit=100)) or {}).get("data") or []
            for x in l: o[int(x[0])] = (float(x[1]), float(x[4]))
            if not l or len(l) < 100: break
            end = min(int(x[0]) for x in l)
    r = pd.DataFrame.from_dict(o, orient="index", columns=["o", "c"]).sort_index(); r = r[(r.index >= t0) & (r.index < t1)]
    cache[k] = r; return r
X = pd.read_csv(f"{D}/cycles-basis.csv", parse_dates=["open", "close"])
T = pd.read_csv(f"{D}/trades-okx.csv", parse_dates=["open", "close"])[["coin", "a", "b", "open", "close", "pos", "th"]]
U = X[X.gross >= 1000].drop_duplicates(["coin", "a", "b", "open", "close"]).merge(T[T.th == 50].drop_duplicates(["coin", "a", "b", "open", "close"]).drop(columns="th"), on=["coin", "a", "b", "open", "close"], how="left", suffixes=("", "_t"))
miss = U.pos.isna()
if miss.any(): U.loc[miss, "pos"] = U[miss].merge(T.drop_duplicates(["coin", "a", "b", "open", "close"]), on=["coin", "a", "b", "open", "close"], how="left").pos_y.values
rows = []
for r in U.itertuples():
    t0 = int(r.open.timestamp() * 1000); t1 = int(r.close.timestamp() * 1000); n = (t1 - t0) // H
    A = kl(r.a, sym[(r.a, r.coin)], t0, t1); B = kl(r.b, sym[(r.b, r.coin)], t0, t1)
    ab = pd.concat([A, B], axis=1, keys=["a", "b"]).dropna()
    if len(ab) < 2: rows.append(dict(coin=r.coin, a=r.a, b=r.b, open=r.open.date(), close=r.close.date(), gross=r.gross, netT=r.netT, cover1m=r.cover, cover1h=len(ab) / n)); continue
    L = lambda x, y: np.log(x / y) * 1e4 * r.pos
    b0 = L(ab[("a", "o")].iloc[0], ab[("b", "o")].iloc[0]); b1 = L(ab[("a", "c")].iloc[-1], ab[("b", "c")].iloc[-1])
    path = L(ab[("a", "c")], ab[("b", "c")])
    rows.append(dict(coin=r.coin, a=r.a, b=r.b, open=r.open.date(), close=r.close.date(), gross=r.gross, netT=r.netT, b0=b0, b1=b1, basis_pnl=b0 - b1, total=r.netT + b0 - b1,
                     up=(path - b0).max(), entry_lag_h=(ab.index[0] - t0) / H, exit_lag_h=(t1 - H - ab.index[-1]) / H, cover1h=len(ab) / n, cover1m=r.cover))
Y = pd.DataFrame(rows); Y.to_csv(f"{D}/cycles1h.csv", index=False)
txt = "== циклы gross>=1000 бп: базис на входе/выходе по 1ч (вход = open свечи open-дня 00:00, выход = close последней свечи до close) ==" + chr(10) + Y.sort_values("gross", ascending=False).round(2).to_string()
open("docs/findings/tk383-cycles1h-2026-10-24.txt", "w", encoding="utf-8").write(txt); print(txt)
