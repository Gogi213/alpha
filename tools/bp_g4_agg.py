"""TK-458 B2 G4.3: агрегатор (6 строк ТФ x направление, якорь g>=ATR/4; кривые порога; Холм по 12). Протокол P-TK458-b2-g4.
python -I tools/bp_g4_agg.py <каталог *-events.csv.gz> <выход-префикс>"""
import sys, glob
import numpy as np, pandas as pd

d = pd.concat([pd.read_csv(f) for f in sorted(glob.glob(sys.argv[1] + "/*-events.csv.gz"))])
d["day"] = d.ts_utc_ms // 86400000; d["year"] = pd.to_datetime(d.ts_utc_ms, unit="ms").dt.year
d["dir"] = d.def_id.str.split(":").str[1]; d["row"] = d.tf + "/" + d.dir
d["dl"] = d.f_ful - d.c_ful; d["d5"] = d.f_50 - d.c_50
first = d.groupby("coin").ts_utc_ms.min(); early = set(first[first < 1577836800000 + 7 * 86400000].index)
rng = np.random.default_rng(458); B = 2000
THR = {"g>0": 0.0, "g>=1/8": .125, "g>=1/4 (якорь)": .25, "g>=1/2": .5}


def boot(g, col):
    m = g.groupby("day")[col].mean().values
    if len(m) < 8: return (np.nan,) * 5 + (len(m),)
    bs = m[rng.integers(0, len(m), (B, len(m)))].mean(1)
    return m.mean(), np.percentile(bs, 2.5), np.percentile(bs, 97.5), (bs <= 0).mean(), (bs >= 0).mean(), len(m)


A = d[d.g_atr >= .25]; rows = []
for r, g in A.groupby("row"):
    rows.append((r,) + boot(g, "dl") + (g.f_ful.mean(), g.c_ful.mean(), boot(g, "d5")[0], g.coin.nunique(), len(g)))
res = pd.DataFrame(rows, columns=["строка", "dlt", "lo", "hi", "p_gt0", "p_lt0", "дней", "fulfill_факт", "fulfill_контр", "dlt_50", "монет", "событий"])
ps = sorted([(r.p_gt0, i, "gt") for i, r in res.iterrows()] + [(r.p_lt0, i, "lt") for i, r in res.iterrows()])
adj = {}; run = 0
for k, (p, i, s) in enumerate(ps):
    run = max(run, min(1, p * (len(ps) - k))); adj[(i, s)] = run
res["holm_gt0"] = [adj[(i, "gt")] for i in res.index]; res["holm_lt0"] = [adj[(i, "lt")] for i in res.index]
yr = []
for r, g0 in A.groupby("row"):
    for y, g in g0.groupby("year"):
        m = boot(g, "dl"); yr.append((r, y, m[0], m[1], m[2], m[5], g.coin.nunique(), g.f_ful.mean(), g.c_ful.mean()))
yr = pd.DataFrame(yr, columns=["строка", "год", "dlt", "lo", "hi", "дней", "монет", "fulfill_факт", "fulfill_контр"])
yr["даны"] = (yr.монет >= 5) & (yr.дней >= 8); yr["знак"] = np.sign(yr.dlt)
ver = []
for i, r in res.iterrows():
    z = yr[(yr.строка == r.строка) & yr.даны]; same = z.знак.nunique() == 1
    ver.append("подтверждено" if r.holm_gt0 < .05 and same and z.знак.iloc[0] > 0 else "оспорено" if r.holm_lt0 < .05 and same and z.знак.iloc[0] < 0 else "не различимо")
res["вердикт"] = ver
sub = []
for r, g0 in A.groupby("row"):
    for nm, g in [("BTC", g0[g0.coin == "BTCUSDT"]), ("ETH", g0[g0.coin == "ETHUSDT"]), ("история с 2020", g0[g0.coin.isin(early)]), ("листинг позже", g0[~g0.coin.isin(early)])]:
        if len(g): sub.append((r, nm, g.groupby("day").dl.mean().mean(), g.coin.nunique(), g.day.nunique()))
sub = pd.DataFrame(sub, columns=["строка", "срез", "dlt", "монет", "дней"])
cur = []
for nm, t in THR.items():
    for r, g in d[d.g_atr >= t].groupby("row"): cur.append((nm, r, g.groupby("day").dl.mean().mean(), len(g)))
cur = pd.DataFrame(cur, columns=["порог", "строка", "dlt", "событий"])
pc = A.groupby(["row", "coin"]).dl.mean().gt(0).groupby("row").agg(["sum", "count"])
o = sys.argv[2]; res.to_csv(o + "-total.csv", index=False); yr.to_csv(o + "-years.csv", index=False); sub.to_csv(o + "-slices.csv", index=False); cur.to_csv(o + "-curve.csv", index=False)
print(res.round(3).to_string()); print(yr.round(3).to_string()); print(sub.round(3).to_string()); print(cur.round(3).to_string()); print(pc)
