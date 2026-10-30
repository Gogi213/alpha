"""TK-458 B2 G8.1: агрегатор (6 строк = шорт/лонг x цель 2R/1.8R/1R; Холм по 12). Протокол P-TK458-b2-g8.
python -I tools/bp_g8_agg.py <каталог *-events.csv.gz> <выход-префикс>"""
import sys, glob
import numpy as np, pandas as pd

D0 = pd.concat([pd.read_csv(f) for f in sorted(glob.glob(sys.argv[1] + "/*-events.csv.gz"))])
D0["day"] = D0.ts_utc_ms // 86400000; D0["year"] = pd.to_datetime(D0.ts_utc_ms, unit="ms").dt.year
D0["dir"] = D0.def_id.str.split(":").str[1]
first = D0.groupby("coin").ts_utc_ms.min(); early = set(first[first < 1577836800000 + 7 * 86400000].index)
rng = np.random.default_rng(458); B = 2000; MS = ["2R", "1.8R", "1R"]


def boot(m):
    if len(m) < 8: return (np.nan,) * 5 + (len(m),)
    bs = m[rng.integers(0, len(m), (B, len(m)))].mean(1)
    return m.mean(), np.percentile(bs, 2.5), np.percentile(bs, 97.5), (bs <= 0).mean(), (bs >= 0).mean(), len(m)


def delta(g, k):
    z = g[g[f"c{k}"].notna()]
    return (z[f"f{k}"] - z[f"c{k}"]).groupby(z.day).mean().values, z


rows = []; yrs = []; subs = []; desc = []
for d, g0 in D0.groupby("dir"):
    for k, mn in enumerate(MS):
        row = f"G8.1 {d} цель {mn}"
        m, z = delta(g0, k); rows.append((row,) + boot(m))
        desc.append((row, len(g0), len(z), z[f"f{k}"].mean(), z[f"c{k}"].mean(), (z[f"f{k}"] > 0).mean(), (z[f"c{k}"] > 0).mean(), z.coin.nunique()))
        for y, g in g0.groupby("year"):
            m, z = delta(g, k); yrs.append((row, y) + boot(m)[:3] + (len(m), z.coin.nunique()))
        for sn, g in [("BTC", g0[g0.coin == "BTCUSDT"]), ("ETH", g0[g0.coin == "ETHUSDT"]), ("история с 2020", g0[g0.coin.isin(early)]), ("листинг позже", g0[~g0.coin.isin(early)])]:
            m, _ = delta(g, k)
            if len(m): subs.append((row, sn, m.mean(), len(m)))
res = pd.DataFrame(rows, columns=["строка", "dlt", "lo", "hi", "p_gt0", "p_lt0", "дней"])
ps = sorted([(r.p_gt0, i, "gt") for i, r in res.iterrows()] + [(r.p_lt0, i, "lt") for i, r in res.iterrows()])
adj = {}; run = 0
for k, (p, i, s) in enumerate(ps):
    run = max(run, min(1, p * (len(ps) - k))); adj[(i, s)] = run
res["holm_gt0"] = [adj[(i, "gt")] for i in res.index]; res["holm_lt0"] = [adj[(i, "lt")] for i in res.index]
yr = pd.DataFrame(yrs, columns=["строка", "год", "dlt", "lo", "hi", "дней", "монет"])
yr["даны"] = (yr.монет >= 5) & (yr.дней >= 8); yr["знак"] = np.sign(yr.dlt)
ver = {}
for _, x in res.iterrows():
    z = yr[(yr.строка == x.строка) & yr.даны]; same = len(z) > 0 and z.знак.nunique() == 1
    ver[x.строка] = "подтверждено" if (x.holm_gt0 < .05 and same and z.знак.iloc[0] > 0) else "оспорено" if (x.holm_lt0 < .05 and same and z.знак.iloc[0] < 0) else "не различимо"
res["вердикт"] = res.строка.map(ver)
ds = pd.DataFrame(desc, columns=["строка", "событий", "с контролем", "R событие", "R контроль", "доля R>0 соб", "доля R>0 контр", "монет"])
sub = pd.DataFrame(subs, columns=["строка", "срез", "dlt", "дней"])
o = sys.argv[2]; res.to_csv(o + "-total.csv", index=False); yr.to_csv(o + "-years.csv", index=False); sub.to_csv(o + "-slices.csv", index=False); ds.to_csv(o + "-desc.csv", index=False)
print(res.round(3).to_string()); print(ds.round(3).to_string()); print(yr.round(3).to_string()); print(sub.round(3).to_string())
