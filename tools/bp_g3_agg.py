"""TK-458 B2 G3.1: агрегатор (6 строк ТФ x направление; Холм по 12). Протокол P-TK458-b2-g3.
python -I tools/bp_g2_agg.py <каталог *-events.csv.gz> <выход-префикс>"""
import sys, glob
import numpy as np, pandas as pd

D0 = pd.concat([pd.read_csv(f) for f in sorted(glob.glob(sys.argv[1] + "/*-events.csv.gz"))])
D0["day"] = D0.ts_utc_ms // 86400000; D0["year"] = pd.to_datetime(D0.ts_utc_ms, unit="ms").dt.year
D0["dir"] = D0.def_id.str.split(":").str[1]
TT = [("G3.1 снятие фрактала k=1, закрытие под уровнем через 10 баров", 0)]
parts = []
for nm, k in TT:
    z = D0[D0[f"f{k}"].notna() & D0[f"c{k}"].notna()].copy()
    z["test"] = nm; z["row"] = nm + " | " + z.tf + "/" + z.dir; z["f"] = z[f"f{k}"]; z["c"] = z[f"c{k}"]; z["dl"] = z.f - z.c
    parts.append(z[["ts_utc_ms", "coin", "tf", "def_id", "day", "year", "test", "row", "f", "c", "dl"]])
A = pd.concat(parts)
first = A.groupby("coin").ts_utc_ms.min(); early = set(first[first < 1577836800000 + 7 * 86400000].index)
rng = np.random.default_rng(458); B = 2000


def boot(g, col):
    m = g.groupby("day")[col].mean().values
    if len(m) < 8: return (np.nan,) * 5 + (len(m),)
    bs = m[rng.integers(0, len(m), (B, len(m)))].mean(1)
    return m.mean(), np.percentile(bs, 2.5), np.percentile(bs, 97.5), (bs <= 0).mean(), (bs >= 0).mean(), len(m)


rows = []
for r, g in A.groupby("row"):
    rows.append((r,) + boot(g, "dl") + (g.f.mean(), g.c.mean(), g.coin.nunique(), len(g), g.groupby(["coin"]).dl.mean().gt(0).sum()))
res = pd.DataFrame(rows, columns=["строка", "dlt", "lo", "hi", "p_gt0", "p_lt0", "дней", "удерж_факт", "удерж_контр", "монет", "событий", "монет_dlt>0"])
ps = sorted([(r.p_gt0, i, "gt") for i, r in res.iterrows()] + [(r.p_lt0, i, "lt") for i, r in res.iterrows()])
adj = {}; run = 0
for k, (p, i, s) in enumerate(ps):
    run = max(run, min(1, p * (len(ps) - k))); adj[(i, s)] = run
res["holm_gt0"] = [adj[(i, "gt")] for i in res.index]; res["holm_lt0"] = [adj[(i, "lt")] for i in res.index]
yr = []
for r, g0 in A.groupby("row"):
    for y, g in g0.groupby("year"):
        m = boot(g, "dl"); yr.append((r, y, m[0], m[1], m[2], m[5], g.coin.nunique(), g.f.mean(), g.c.mean()))
yr = pd.DataFrame(yr, columns=["строка", "год", "dlt", "lo", "hi", "дней", "монет", "удерж_факт", "удерж_контр"])
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
print("воронка: ног-событий (хоть один тест)", len(D0), "; с касанием и контролем по тесту:", {nm[:4]: int((D0[f"f{k}"].notna() & D0[f"c{k}"].notna()).sum()) for nm, k in TT})
o = sys.argv[2]; res.to_csv(o + "-total.csv", index=False); yr.to_csv(o + "-years.csv", index=False); sub.to_csv(o + "-slices.csv", index=False)
print(res.round(3).to_string()); print(yr.round(3).to_string()); print(sub.round(3).to_string())
