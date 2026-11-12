#!/usr/bin/env python3
"""TK-383 (возврат v2): (1) распределение дохода цикла (gross/нетто тейкер) по парам, Bitget/Gate, месяцам; циклы >=24%;
(2) базис/MAE на 1м для циклов: OKX θ=50 и все gross>=1000 бп (OKX 1м, Gate 5м, остальные как в tk383-basis1m.py).
Выход: docs/findings/tk383-cycles-2026-10-24.txt, data/tk383/cycles-basis.csv."""
import json, sys, time
import numpy as np, pandas as pd, requests
sys.path.insert(0, "tools/compute")
import importlib.util
sp = importlib.util.spec_from_file_location("b1m", "tools/compute/tk383-basis1m.py"); b1m = importlib.util.module_from_spec(sp); sp.loader.exec_module(b1m)
D = "data/tk383"; M = 60000
t = pd.read_csv(f"{D}/trades-okx.csv", parse_dates=["open", "close"])
t["okx"] = (t.a == "okx") | (t.b == "okx")
q = lambda s: pd.Series({"n": len(s), "p50": s.quantile(.5), "p90": s.quantile(.9), "p99": s.quantile(.99), "max": s.max(), "mean": s.mean()}).round(1)
out = []
for th in (50, 10):
    u = t[t.th == th]
    out.append(f"== распределение gross / netT цикла, бп, θ={th} (цикл = вход→выход) ==")
    for nm, g in [("все", u), ("Bitget/Gate", u[((u.a == "bitget") & (u.b == "gate")) | ((u.a == "gate") & (u.b == "bitget"))])]:
        for m, gm in g.groupby("month"):
            out.append(f"{nm} {m} gross: " + q(gm.gross).to_dict().__repr__()); out.append(f"{nm} {m} netT:  " + q(gm.netT).to_dict().__repr__())
    pr = u.assign(pair=u.a + "/" + u.b).groupby("pair").agg(n=("gross", "size"), p50=("gross", "median"), p90=("gross", lambda s: s.quantile(.9)), mx=("gross", "max"), netT=("netT", "mean"))
    out.append(pr.round(1).sort_values("mx", ascending=False).head(25).to_string())
for lim in (2400, 1000):
    u = t.drop_duplicates(["coin", "a", "b", "open", "close"]); u = u[u.gross >= lim].sort_values("gross", ascending=False)
    out.append(f"== циклы gross >= {lim} бп: {len(u)} ==\n" + u[["coin", "a", "b", "th", "open", "close", "days", "gross", "netT"]].round(0).to_string())
# --- базис
okx = {v: k for k, v in json.load(open(f"{D}/lists-okx.json")).items()}
sym = b1m.sym; sym.update({("okx", c): s for c, s in okx.items()})
def okx_kl(s, t0, t1):
    fn = f"{D}/k1m/okx_{s}_{t0}_{t1}.pkl"
    import os
    if os.path.exists(fn): return pd.read_pickle(fn)
    o = {}; end = t1
    while end > t0:
        j = b1m.get("https://www.okx.com/api/v5/market/history-candles", dict(instId=s, bar="1m", after=end, limit=100))
        l = (j or {}).get("data") or []
        if not l: break
        for x in l: o[int(x[0])] = float(x[4])
        end = min(int(x[0]) for x in l)
        if end <= t0 or len(l) < 100: break
    ser = pd.Series(o, dtype=float).sort_index(); ser = ser[(ser.index >= t0) & (ser.index < t1)]; ser.to_pickle(fn); return ser
def kl(e, s, t0, t1): return okx_kl(s, t0, t1) if e == "okx" else b1m.kl(e, s, t0, t1)
c = t.drop_duplicates(["coin", "a", "b", "open", "close"]).merge(t[t.th == 50][["coin", "a", "b", "open", "close", "pos"]].drop_duplicates(), on=["coin", "a", "b", "open", "close"], how="left", suffixes=("", "_50"))
c["pos"] = c.pos_50.where(c.pos_50.notna(), c.pos)
sel = c[((c.th == 50) & c.okx) | (c.gross >= 1000)].copy()
# pos для θ=50 берём как в trades; для остальных — из строки
from concurrent.futures import ThreadPoolExecutor
t0f = lambda r: int((r.open - pd.Timedelta(minutes=1)).timestamp() * 1000)
jobs = set()
for r in sel.itertuples():
    for e in (r.a, r.b):
        if (e, r.coin) in sym: jobs.add((e, sym[(e, r.coin)], t0f(r), int(r.close.timestamp() * 1000)))
jobs = sorted(jobs); print("задач", len(jobs), flush=True)
with ThreadPoolExecutor(16) as p: res = dict(zip(jobs, p.map(lambda j: kl(*j), jobs)))
rows = []
for r in sel.itertuples():
    t0, t1 = t0f(r), int(r.close.timestamp() * 1000)
    A = res.get((r.a, sym.get((r.a, r.coin)), t0, t1)); B = res.get((r.b, sym.get((r.b, r.coin)), t0, t1))
    if A is None or B is None or len(A) < 2 or len(B) < 2: continue
    ab = pd.concat([A, B], axis=1, keys=["a", "b"]).dropna()
    if len(ab) < 2: continue
    b = np.log(ab.a / ab.b) * 1e4 * r.pos
    rows.append((r.coin, r.a, r.b, r.th, r.open.date(), r.close.date(), r.gross, r.netT, b.iloc[0], b.iloc[-1], (b - b.iloc[0]).max(), (b - b.iloc[0]).min(), len(ab) / ((t1 - t0) // M / (5 if "gate" in (r.a, r.b) else 1)), abs(np.log(ab.a / ab.b)).iloc[0] * 1e4))
X = pd.DataFrame(rows, columns="coin a b th open close gross netT b0 b1 up down cover absb0".split())
X["basis_pnl"] = X.b0 - X.b1; X["total"] = X.netT + X.basis_pnl
X["okx"] = (X.a == "okx") | (X.b == "okx"); X["month"] = X.open.astype(str).str[:7]
X.to_csv(f"{D}/cycles-basis.csv", index=False)
out.append(f"== базис 1м: выбрано {len(sel)} циклов, покрыто {len(X)} ==")
g50 = X[(X.th == 50) & X.okx]
out.append("OKX θ=50 по месяцам: n, фандинг netT, базис, итого, медиана итого, MAE up p50/p90/p99")
for m, gm in g50.groupby("month"): out.append(f"{m} n={len(gm)} netT={gm.netT.mean():.1f} базис={gm.basis_pnl.mean():.1f} итого={gm.total.mean():.1f} мед={gm.total.median():.1f} MAE={gm.up.quantile(.5):.0f}/{gm.up.quantile(.9):.0f}/{gm.up.quantile(.99):.0f}")
out.append(f"OKX θ=50 сумма итого {g50.total.sum():.0f}; без топ-5 монет: " + f"{g50.total.sum() - g50.groupby('coin').total.sum().nlargest(5).sum():.0f}")
out.append("== циклы gross>=1000: базис ==\n" + X[X.gross >= 1000].drop_duplicates(["coin","a","b","open","close"]).sort_values("gross", ascending=False)[["coin","a","b","open","close","gross","netT","b0","b1","basis_pnl","total","up","down","absb0","cover"]].round(0).to_string())
open("docs/findings/tk383-cycles-2026-10-24.txt", "w", encoding="utf-8").write("\n".join(out))
print("\n".join(out))
