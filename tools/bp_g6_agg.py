"""TK-458 B2 G6.4: агрегатор Δ по событиям: итог, годы, BTC/ETH, история с 2020, бутстреп по дням, Холм K=8, вердикт §3 п.3.
python -I tools/bp_g6_agg.py <каталог с *-events.csv.gz> <выход-префикс>"""
import sys, glob
import numpy as np, pandas as pd

d = pd.concat([pd.read_csv(f) for f in sorted(glob.glob(sys.argv[1] + "/*-events.csv.gz"))])
d["win"] = d.def_id.str.split(":").str[1]; d["day"] = d.ts_utc_ms // 86400000
d["year"] = pd.to_datetime(d.ts_utc_ms, unit="ms").dt.year
first = d.groupby("coin").ts_utc_ms.min(); early = set(first[first < 1577836800000 + 7 * 86400000].index)
rng = np.random.default_rng(458); B = 2000


def boot(x):
    """x: Series Δ по (монета,день) -> дневные средние; бутстреп по дням"""
    m = x.groupby(level="day").mean().values
    if len(m) < 8: return np.nan, np.nan, np.nan, len(m)
    bs = m[rng.integers(0, len(m), (B, len(m)))].mean(1)
    return m.mean(), np.percentile(bs, 2.5), np.percentile(bs, 97.5), (bs <= 0).mean(), (bs >= 0).mean(), len(m)


rows = []
for w, g in d.groupby("win"):
    x = g.set_index(["coin", "day"]).delta; x.index.names = ["coin", "day"]
    xs = g.set_index(["coin", "day"]); 
    m, lo, hi, p_le, p_ge, n = boot(g.set_index("day").delta.rename_axis("day").to_frame().assign(c=1).delta.groupby(level=0).mean().to_frame("v").v.pipe(lambda s: pd.Series(g.groupby("day").delta.mean())))
    rows.append((w, "итог", m, lo, hi, p_le, p_ge, n, g.coin.nunique()))
res = pd.DataFrame(rows, columns=["окно", "срез", "dlt", "lo", "hi", "p_gt0", "p_lt0", "дней", "монет"])
# Холм по K=8: 4 окна x 2 стороны
ps = sorted([(r.p_gt0, i, "gt") for i, r in res.iterrows()] + [(r.p_lt0, i, "lt") for i, r in res.iterrows()])
adj = {}; run = 0
for k, (p, i, s) in enumerate(ps):
    run = max(run, min(1, p * (len(ps) - k))); adj[(i, s)] = run
res["holm_gt0"] = [adj[(i, "gt")] for i in res.index]; res["holm_lt0"] = [adj[(i, "lt")] for i in res.index]
yr = []
for w, g in d.groupby("win"):
    for y, gy in g.groupby("year"):
        dm = gy.groupby("day").delta.agg(["mean"]).iloc[:, 0]
        yr.append((w, y, dm.mean(), len(dm), gy.coin.nunique(), np.sign(dm.mean())))
yr = pd.DataFrame(yr, columns=["окно", "год", "dlt", "дней", "монет", "знак"])
yr["даны"] = (yr.монет >= 5) & (yr.дней >= 8)
ver = {}
for i, r in res.iterrows():
    z = yr[(yr.окно == r.окно) & yr.даны]; same = z.знак.nunique() == 1
    ver[r.окно] = ("подтверждено (активнее)" if r.holm_gt0 < .05 and r.dlt > 0 and same and z.знак.iloc[0] > 0
                   else "оспорено (тише)" if r.holm_lt0 < .05 and r.dlt < 0 and same and z.знак.iloc[0] < 0 else "не различимо")
res["вердикт"] = res.окно.map(ver)
sub = []
for w, g in d.groupby("win"):
    for nm, gg in [("BTC", g[g.coin == "BTCUSDT"]), ("ETH", g[g.coin == "ETHUSDT"]), ("история с 2020", g[g.coin.isin(early)]), ("листинг позже", g[~g.coin.isin(early)])]:
        if len(gg): sub.append((w, nm, gg.groupby("day").delta.mean().mean(), gg.coin.nunique(), gg.groupby("day").ngroups))
sub = pd.DataFrame(sub, columns=["окно", "срез", "dlt", "монет", "дней"])
pc = d.groupby(["win", "coin"]).delta.mean().gt(0).groupby("win").agg(["sum", "count"]).rename(columns={"sum": "монет_dlt>0", "count": "монет"})
o = sys.argv[2]; res.to_csv(o + "-total.csv", index=False); yr.to_csv(o + "-years.csv", index=False); sub.to_csv(o + "-slices.csv", index=False); pc.to_csv(o + "-coins.csv")
print(res.round(3).to_string()); print(yr.round(3).to_string()); print(sub.round(3).to_string()); print(pc)
