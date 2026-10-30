"""TK-458 B2 G6.1/G6.2: агрегатор. Строки: G6.1 Лондон/NY, G6.2 хай→лой/лой→хай. B=4000 seed 457; Холм здесь по 8 — предварительный, окончательный K=70 в bp_b2_holm.py.
python -I tools/bp_g61_agg.py <каталог *-events.csv.gz> <выход-префикс>"""
import sys, glob
import numpy as np, pandas as pd

d = pd.concat([pd.read_csv(f) for f in sorted(glob.glob(sys.argv[1] + "/*-events.csv.gz"))])
d["day"] = d.ts_utc_ms // 86400000; d["year"] = pd.to_datetime(d.ts_utc_ms, unit="ms").dt.year
first = d.groupby("coin").ts_utc_ms.min(); early = set(first[first < 1577836800000 + 7 * 86400000].index)
rng = np.random.default_rng(457); B = 4000
ROWS = [("G6.1 Лондон (60–360 мин)", "L"), ("G6.1 NY (360–540 мин)", "N"), ("G6.2 снят хай → цель лой в NY", "u"), ("G6.2 снят лой → цель хай в NY", "d")]


def sel(k):
    g = d[d[f"f{k}"].notna() & d[f"c{k}"].notna()].copy(); g["dl"] = g[f"f{k}"] - g[f"c{k}"]; return g


def boot(g):
    m = g.groupby("day").dl.mean().values
    if len(m) < 8: return (np.nan,) * 5 + (len(m),)
    bs = m[rng.integers(0, len(m), (B, len(m)))].mean(1)
    return m.mean(), np.percentile(bs, 2.5), np.percentile(bs, 97.5), (bs <= 0).mean(), (bs >= 0).mean(), len(m)


res, yr, sub = [], [], []
for nm, k in ROWS:
    g = sel(k); b = boot(g)
    res.append((nm,) + b + (g[f"f{k}"].mean(), g[f"c{k}"].mean(), g.coin.nunique(), len(g), g.groupby("coin").dl.mean().gt(0).sum()))
    for y, gy in g.groupby("year"):
        m = boot(gy); yr.append((nm, y, m[0], m[1], m[2], m[5], gy.coin.nunique(), gy[f"f{k}"].mean(), gy[f"c{k}"].mean()))
    for sn, gs in [("BTC", g[g.coin == "BTCUSDT"]), ("ETH", g[g.coin == "ETHUSDT"]), ("история с 2020", g[g.coin.isin(early)]), ("листинг позже", g[~g.coin.isin(early)])]:
        if len(gs): sub.append((nm, sn, gs.groupby("day").dl.mean().mean(), gs.coin.nunique(), gs.day.nunique()))
res = pd.DataFrame(res, columns=["строка", "dlt", "lo", "hi", "p_gt0", "p_lt0", "дней", "доля_факт", "доля_контр", "монет", "событий", "монет_dlt>0"])
yr = pd.DataFrame(yr, columns=["строка", "год", "dlt", "lo", "hi", "дней", "монет", "доля_факт", "доля_контр"])
yr["даны"] = (yr.монет >= 5) & (yr.дней >= 8); yr["знак"] = np.sign(yr.dlt)
ps = sorted([(r.p_gt0, i, "gt") for i, r in res.iterrows()] + [(r.p_lt0, i, "lt") for i, r in res.iterrows()])
adj = {}; run = 0
for k, (p, i, s) in enumerate(ps):
    run = max(run, min(1, p * (len(ps) - k))); adj[(i, s)] = run
res["holm_gt0"] = [adj[(i, "gt")] for i in res.index]; res["holm_lt0"] = [adj[(i, "lt")] for i in res.index]
ver = []
for i, r in res.iterrows():
    z = yr[(yr.строка == r.строка) & yr.даны]; same = z.знак.nunique() == 1
    ver.append("подтверждено" if r.holm_gt0 < .05 and same and z.знак.iloc[0] > 0 else "оспорено" if r.holm_lt0 < .05 and same and z.знак.iloc[0] < 0 else "не различимо")
res["вердикт"] = ver
# описательно: доли сессий среди снятий (факт), Франкфурт = остаток
o = sys.argv[2]; res.to_csv(o + "-total.csv", index=False); yr.to_csv(o + "-years.csv", index=False)
pd.DataFrame(sub, columns=["строка", "срез", "dlt", "монет", "дней"]).to_csv(o + "-slices.csv", index=False)
print(res.round(4).to_string()); print(yr.round(3).to_string())
