"""TK-458 B2 G6.3: агрегатор (2 строки, Холм по 4 односторонним). Протокол P-TK458-b2-g63.
python -I tools/bp_g63_agg.py <каталог *-freq-events.csv.gz и *-out-events.csv.gz> <выход-префикс>"""
import sys, glob
import numpy as np, pandas as pd

src = sys.argv[1]
F = pd.concat([pd.read_csv(f) for f in sorted(glob.glob(src + "/*-freq-events.csv.gz"))])
O0 = pd.concat([pd.read_csv(f) for f in sorted(glob.glob(src + "/*-out-events.csv.gz"))])
O0["delta"] = O0.f_ful - O0.c_ful
R1, R2 = "1 частота (Лондон − остальное)", "2 исход fulfill (− C2)"
F["row"] = R1; O = O0[O0.g_atr >= .25].copy(); O["row"] = R2
d = pd.concat([F[["ts_utc_ms", "coin", "row", "delta"]], O[["ts_utc_ms", "coin", "row", "delta"]]])
d["day"] = d.ts_utc_ms // 86400000; d["year"] = pd.to_datetime(d.ts_utc_ms, unit="ms").dt.year
SIGN = {R1: -1, R2: 1}  # заявленный знак Δ
first = d.groupby("coin").ts_utc_ms.min(); early = set(first[first < 1577836800000 + 7 * 86400000].index)
rng = np.random.default_rng(458); B = 2000


def boot(g):
    m = g.groupby("day").delta.mean().values
    if len(m) < 8: return (np.nan,) * 5 + (len(m),)
    bs = m[rng.integers(0, len(m), (B, len(m)))].mean(1)
    return m.mean(), np.percentile(bs, 2.5), np.percentile(bs, 97.5), (bs <= 0).mean(), (bs >= 0).mean(), len(m)


rows = []
for r, g in d.groupby("row"): rows.append((r,) + boot(g) + (g.coin.nunique(), len(g)))
res = pd.DataFrame(rows, columns=["строка", "dlt", "lo", "hi", "p_gt0", "p_lt0", "дней", "монет", "событий"])
ps = sorted([(r.p_gt0, i, "gt") for i, r in res.iterrows()] + [(r.p_lt0, i, "lt") for i, r in res.iterrows()])
adj = {}; run = 0
for k, (p, i, s) in enumerate(ps):
    run = max(run, min(1, p * (len(ps) - k))); adj[(i, s)] = run
res["holm_gt0"] = [adj[(i, "gt")] for i in res.index]; res["holm_lt0"] = [adj[(i, "lt")] for i in res.index]
yr = []
for r, g0 in d.groupby("row"):
    for y, g in g0.groupby("year"):
        m = boot(g); yr.append((r, y, m[0], m[1], m[2], m[5], g.coin.nunique()))
yr = pd.DataFrame(yr, columns=["строка", "год", "dlt", "lo", "hi", "дней", "монет"])
yr["даны"] = (yr.монет >= 5) & (yr.дней >= 8); yr["знак"] = np.sign(yr.dlt)
ver = []
for i, r in res.iterrows():
    z = yr[(yr.строка == r.строка) & yr.даны]; same = z.знак.nunique() == 1; s = SIGN[r.строка]
    pos = r.holm_gt0 < .05 and same and z.знак.iloc[0] > 0; neg = r.holm_lt0 < .05 and same and z.знак.iloc[0] < 0
    ver.append("подтверждено" if (pos if s > 0 else neg) else "оспорено" if (neg if s > 0 else pos) else "не различимо")
res["вердикт"] = ver
sub = []
for r, g0 in d.groupby("row"):
    for nm, g in [("BTC", g0[g0.coin == "BTCUSDT"]), ("ETH", g0[g0.coin == "ETHUSDT"]), ("история с 2020", g0[g0.coin.isin(early)]), ("листинг позже", g0[~g0.coin.isin(early)])]:
        if len(g): sub.append((r, nm, g.groupby("day").delta.mean().mean(), g.coin.nunique(), g.day.nunique()))
sub = pd.DataFrame(sub, columns=["строка", "срез", "dlt", "монет", "дней"])
cur = []
for t in (0, .125, .25, .5):
    gg = O0[O0.g_atr >= t]
    cur.append((t, gg.groupby(gg.ts_utc_ms // 86400000).delta.mean().mean(), len(gg)))
cur = pd.DataFrame(cur, columns=["порог g/ATR", "dlt", "событий"])
pc = d.groupby(["row", "coin"]).delta.mean().gt(0).groupby("row").agg(["sum", "count"])
o = sys.argv[2]; res.to_csv(o + "-total.csv", index=False); yr.to_csv(o + "-years.csv", index=False); sub.to_csv(o + "-slices.csv", index=False); cur.to_csv(o + "-curve.csv", index=False)
print(res.round(4).to_string()); print(yr.round(4).to_string()); print(sub.round(4).to_string()); print(cur.round(4).to_string()); print(pc)
