"""TK-458 B2 G7: агрегатор (4 строки x 2 контроля; Холм по 16). Протокол P-TK458-b2-g7.
python -I tools/bp_g7_agg.py <каталог *-events.csv.gz> <выход-префикс>"""
import sys, glob
import numpy as np, pandas as pd

D0 = pd.concat([pd.read_csv(f) for f in sorted(glob.glob(sys.argv[1] + "/*-events.csv.gz"))])
D0["day"] = D0.ts_utc_ms // 86400000; D0["year"] = pd.to_datetime(D0.ts_utc_ms, unit="ms").dt.year
D0["dir"] = D0.def_id.str.split(":").str[1]
first = D0.groupby("coin").ts_utc_ms.min(); early = set(first[first < 1577836800000 + 7 * 86400000].index)
rng = np.random.default_rng(458); B = 2000
TT = [("G7.1 D1-фрактал в сторону", "d1"), ("G7.2 H1-имбаланс-цель", "imb")]
st = lambda m: (m.mean(), *np.percentile(m[rng.integers(0, len(m), (B, len(m)))].mean(1), [2.5, 97.5]))


def boot(m):
    if len(m) < 8: return (np.nan,) * 5 + (len(m),)
    bs = m[rng.integers(0, len(m), (B, len(m)))].mean(1)
    return m.mean(), np.percentile(bs, 2.5), np.percentile(bs, 97.5), (bs <= 0).mean(), (bs >= 0).mean(), len(m)


def deltas(g, fl):
    """-> {контроль: серия по дням}: C1 внутри событий с фильтром; U — день: фильтр минус без фильтра"""
    z = g[g[fl] == 1]; z = z[z.c0.notna()]
    c1 = (z.f0 - z.c0).groupby(z.day).mean()
    a = g[g[fl] == 1].groupby("day").f0.mean(); b = g[g[fl] == 0].groupby("day").f0.mean()
    u = (a - b).dropna()
    return {"C1": c1.values, "без фильтра": u.values}, len(z), len(g)


rows = []; yrs = []; subs = []
for nm, fl in TT:
    for d, g0 in D0.groupby("dir"):
        row = f"{nm} | H1/{d}"
        for cn, m in deltas(g0, fl)[0].items():
            rows.append((row, cn) + boot(m))
        for y, g in g0.groupby("year"):
            for cn, m in deltas(g, fl)[0].items():
                yrs.append((row, cn, y) + boot(m)[:3] + (len(m), g[g[fl] == 1].coin.nunique()))
        for sn, g in [("BTC", g0[g0.coin == "BTCUSDT"]), ("ETH", g0[g0.coin == "ETHUSDT"]), ("история с 2020", g0[g0.coin.isin(early)]), ("листинг позже", g0[~g0.coin.isin(early)])]:
            for cn, m in deltas(g, fl)[0].items():
                if len(m): subs.append((row, cn, sn, m.mean(), len(m)))
        dl, nz, ng = deltas(g0, fl)
        print(row, "воронка: событий", ng, "с фильтром", nz, "доля с фильтром", round((g0[fl] == 1).mean(), 3), "монет", g0[g0[fl] == 1].coin.nunique())
res = pd.DataFrame(rows, columns=["строка", "контроль", "dlt", "lo", "hi", "p_gt0", "p_lt0", "дней"])
ps = sorted([(r.p_gt0, i, "gt") for i, r in res.iterrows()] + [(r.p_lt0, i, "lt") for i, r in res.iterrows()])
adj = {}; run = 0
for k, (p, i, s) in enumerate(ps):
    run = max(run, min(1, p * (len(ps) - k))); adj[(i, s)] = run
res["holm_gt0"] = [adj[(i, "gt")] for i in res.index]; res["holm_lt0"] = [adj[(i, "lt")] for i in res.index]
yr = pd.DataFrame(yrs, columns=["строка", "контроль", "год", "dlt", "lo", "hi", "дней", "монет"])
yr["даны"] = (yr.монет >= 5) & (yr.дней >= 8); yr["знак"] = np.sign(yr.dlt)
ver = {}
for r, g in res.groupby("строка"):
    ok_up = ok_dn = True
    for _, x in g.iterrows():
        z = yr[(yr.строка == r) & (yr.контроль == x.контроль) & yr.даны]; same = len(z) > 0 and z.знак.nunique() == 1
        ok_up &= bool(x.holm_gt0 < .05 and same and z.знак.iloc[0] > 0); ok_dn &= bool(x.holm_lt0 < .05 and same and z.знак.iloc[0] < 0)
    ver[r] = "подтверждено" if ok_up else "оспорено" if ok_dn else "не различимо"
res["вердикт"] = res.строка.map(ver)
sub = pd.DataFrame(subs, columns=["строка", "контроль", "срез", "dlt", "дней"])
o = sys.argv[2]; res.to_csv(o + "-total.csv", index=False); yr.to_csv(o + "-years.csv", index=False); sub.to_csv(o + "-slices.csv", index=False)
print(res.round(3).to_string()); print(yr.round(3).to_string()); print(sub.round(3).to_string())
