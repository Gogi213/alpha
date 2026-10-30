"""TK-458 B2 G5: агрегатор bpP-01 (4 глубины, Холм по 8 односторонним) и bpP-02 (цикличность по годам).
python -I tools/bp_g5_agg.py <каталог *-events.csv.gz> <выход-префикс>"""
import sys, glob
import numpy as np, pandas as pd

d = pd.concat([pd.read_csv(f) for f in sorted(glob.glob(sys.argv[1] + "/*-events.csv.gz"))])
d["day"] = d.ts_utc_ms // 86400000; d["year"] = pd.to_datetime(d.ts_utc_ms, unit="ms").dt.year
first = d.groupby("coin").ts_utc_ms.min(); early = set(first[first < 1577836800000 + 7 * 86400000].index)
rng = np.random.default_rng(458); B = 2000; NAME = ["0,25", "0,5", "1", "без предела"]
for i in range(4): d[f"d{i}"] = d[f"f{i}"] - d[f"c{i}"]


def boot(g, col):
    m = g.groupby("day")[col].mean().values
    if len(m) < 8: return (np.nan,) * 5 + (len(m),)
    bs = m[rng.integers(0, len(m), (B, len(m)))].mean(1)
    return m.mean(), np.percentile(bs, 2.5), np.percentile(bs, 97.5), (bs <= 0).mean(), (bs >= 0).mean(), len(m)


rows = [(NAME[i],) + boot(d, f"d{i}") + (d[f"f{i}"].mean(), d[f"c{i}"].mean(), d.coin.nunique(), len(d)) for i in range(4)]
res = pd.DataFrame(rows, columns=["глубина", "dlt", "lo", "hi", "p_gt0", "p_lt0", "дней", "доля_факт", "доля_контр", "монет", "событий"])
ps = sorted([(r.p_gt0, i, "gt") for i, r in res.iterrows()] + [(r.p_lt0, i, "lt") for i, r in res.iterrows()])
adj = {}; run = 0
for k, (p, i, s) in enumerate(ps):
    run = max(run, min(1, p * (len(ps) - k))); adj[(i, s)] = run
res["holm_gt0"] = [adj[(i, "gt")] for i in res.index]; res["holm_lt0"] = [adj[(i, "lt")] for i in res.index]
yr = []
for i in range(4):
    for y, g in d.groupby("year"):
        m = boot(g, f"d{i}"); yr.append((NAME[i], y, m[0], m[1], m[2], m[5], g.coin.nunique(), g[f"f{i}"].mean(), g[f"c{i}"].mean()))
yr = pd.DataFrame(yr, columns=["глубина", "год", "dlt", "lo", "hi", "дней", "монет", "доля_факт", "доля_контр"])
yr["даны"] = (yr.монет >= 5) & (yr.дней >= 8); yr["знак"] = np.sign(yr.dlt)
ver = []
for i, r in res.iterrows():
    z = yr[(yr.глубина == r.глубина) & yr.даны]; same = z.знак.nunique() == 1
    ver.append("подтверждено" if r.holm_gt0 < .05 and same and z.знак.iloc[0] > 0 else "оспорено" if r.holm_lt0 < .05 and same and z.знак.iloc[0] < 0 else "не различимо")
res["вердикт"] = ver
z = yr[(yr.глубина == NAME[3]) & yr.даны]
cyc = "подтверждено (смена знака)" if (z.lo > 0).any() and (z.hi < 0).any() else "оспорено (знак один во всех годах)" if z.знак.nunique() == 1 else "не различимо"
sub = []
for nm, g in [("BTC", d[d.coin == "BTCUSDT"]), ("ETH", d[d.coin == "ETHUSDT"]), ("история с 2020", d[d.coin.isin(early)]), ("листинг позже", d[~d.coin.isin(early)])]:
    if len(g): sub.append((nm, g.groupby("day").d3.mean().mean(), g.coin.nunique(), g.day.nunique()))
sub = pd.DataFrame(sub, columns=["срез", "dlt(без предела)", "монет", "дней"])
pc = d.groupby("coin").d3.mean().gt(0).agg(["sum", "count"])
o = sys.argv[2]; res.to_csv(o + "-total.csv", index=False); yr.to_csv(o + "-years.csv", index=False); sub.to_csv(o + "-slices.csv", index=False)
print(res.round(3).to_string()); print(yr.round(3).to_string()); print(sub.round(3).to_string()); print("монет d>0:", dict(pc), "| bpP-02:", cyc)
