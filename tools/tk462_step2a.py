#!/usr/bin/env python3
"""TK-462 шаг 2а (P-TK462 §3, §4 H-F2, §5 C1/C2/C7/C9): маргинальные вклады причин из выписки сделок (без минуток).
python tk462_step2a.py <entries.csv.gz O1/O2> <o3_dir> <out.csv> [<out_c9.csv>]
net = gross - комиссия В-63 - фандинг (как шаг 1). Вклад в минус = (mean_net[z=0] - mean_net[z=1]) * доля(z=1), bps на сделку:
> 0 — в z=1 хуже остального. Интервал и SE — бутстрап по монетам (B=4000, seed 462); p — нормальное приближение по SE
(B=4000 не разрешает p < 2,5e-4, а порог Холма при K=222 — 2,3e-4). Холм по K=222 (§7); нерассчитанные строки остаются в семействе.
Контроль §3 п.4 (случайные входы в те же окна) здесь НЕ считается -> вердикт «предв.», не итог. O3 — три цели сложены (кластер = монета)."""
import glob, sys
import numpy as np, pandas as pd
from math import erf, sqrt

MK, TK, B, K = 1.26, 3.15, 4000, 222
ent, o3d, out = sys.argv[1:4]
out9 = sys.argv[4] if len(sys.argv) > 4 else None
H8 = 8 * 3600 * 1000

a = pd.read_csv(ent, usecols=["sym", "tf", "form", "t0", "reason", "gross", "n_mk", "fund", "risk", "bars", "year"])
a["obj"] = np.where(a.form.astype(str).str.upper().str.startswith("B"), "O2", "O1")
a["coin"] = a.sym
a["fee"] = a.n_mk * MK + (2 - a.n_mk) * TK
a["t_in"] = a.t0; a["t_out"] = a.t0 + (a.bars * a.tf * 60000).round()
parts = [a[["coin", "obj", "year", "t_in", "t_out", "reason", "gross", "fee", "fund", "risk"]]]
for p in sorted(glob.glob(o3d + "/*.csv.gz")):
    d = pd.read_csv(p, usecols=["coin", "t_in_ms", "t_out_ms", "reason", "gross_bps", "fund_bps", "rho_bps"])
    d = d.rename(columns={"t_in_ms": "t_in", "t_out_ms": "t_out", "gross_bps": "gross", "fund_bps": "fund", "rho_bps": "risk"})
    d["obj"] = "O3"; d["fee"] = 2 * TK; d["year"] = pd.to_datetime(d.t_in, unit="ms", utc=True).dt.year
    parts.append(d[["coin", "obj", "year", "t_in", "t_out", "reason", "gross", "fee", "fund", "risk"]])
r = pd.concat(parts, ignore_index=True)
r = r[r.fund.notna()].copy()
r["net"] = r.gross - r.fee - r.fund
dt = pd.to_datetime(r.t_in, unit="ms", utc=True)
r["hour"] = dt.dt.hour; r["dow"] = dt.dt.dayofweek
r["crossed"] = (r.t_out // H8) > (r.t_in // H8)

rng = np.random.default_rng(462)
def contrib(g, z):
    """g: сделки объекта; z: булев массив. Возвращает вклад в минус, bps на сделку (точка, lo, hi, se, p), n1, монет."""
    c = pd.DataFrame({"coin": g.coin.values, "net": g.net.values, "z": z})
    c["n1"] = c.z.astype(int); c["s1"] = np.where(c.z, c.net, 0.0)
    c = c.groupby("coin").agg(n=("net", "size"), s=("net", "sum"), n1=("n1", "sum"), s1=("s1", "sum"))
    n, s, n1, s1 = (c[x].values.astype(float) for x in ["n", "s", "n1", "s1"])
    def f(w):
        N, S, N1, S1 = w @ n, w @ s, w @ n1, w @ s1
        with np.errstate(all="ignore"):
            return ((S - S1) / (N - N1) - S1 / N1) * N1 / N
    k = len(c); pt = f(np.ones((1, k)))[0]
    bs = f(rng.multinomial(k, np.ones(k) / k, size=B).astype(float)); bs = bs[np.isfinite(bs)]
    se = bs.std(ddof=1)
    p = 0.5 * (1 - erf(pt / se / sqrt(2))) if se > 0 and np.isfinite(pt) else np.nan
    return pt, np.quantile(bs, .025), np.quantile(bs, .975), se, p, int(n1.sum()), int((c.n1 > 0).sum())

def terc(x):
    q = np.nanquantile(x, [1 / 3, 2 / 3]); return np.where(x <= q[0], 0, np.where(x <= q[1], 1, 2)), q

rows, c9 = [], []
for obj, g in r.groupby("obj"):
    g = g.reset_index(drop=True)
    defs = [("H-F2", "пересёк расчёт 00/08/16 UTC", g.crossed.values)]
    for nm, lo, hi in [("Азия 00–08", 0, 8), ("Европа 08–16", 8, 16), ("США 16–24", 16, 24)]:
        defs.append(("C1", nm, ((g.hour >= lo) & (g.hour < hi)).values))
    for i, nm in enumerate(["пн", "вт", "ср", "чт", "пт", "сб", "вс"]):
        defs.append(("C2", nm, (g.dow == i).values))
    hv = g.risk.notna().values & (g.risk.values > 0)
    if hv.mean() > 0.5:
        ratio = np.where(hv, g.fee.values / np.where(hv, g.risk.values, 1), np.nan)
        tc, q = terc(ratio[hv]); t = np.full(len(g), -1); t[hv] = tc
        for i, nm in enumerate(["низкая", "средняя", "высокая"]):
            defs.append(("C7", f"доля комиссий/риск: {nm} (q={q.round(3).tolist()})", t == i))
    for fam, nm, z in defs:
        if z.sum() < 30 or (~z).sum() < 30:
            rows.append(dict(obj=obj, fam=fam, row=nm, n1=int(z.sum()), verdict="не оценено (N<30)")); continue
        pt, lo, hi, se, p, n1, nc = contrib(g, z)
        rows.append(dict(obj=obj, fam=fam, row=nm, n1=n1, coins1=nc, share=round(n1 / len(g), 3), mean_z1=round(g.net[z].mean(), 3),
                         mean_z0=round(g.net[~z].mean(), 3), contrib=round(pt, 3), lo=round(lo, 3), hi=round(hi, 3), se=round(se, 3), p=p, idx=(obj, fam, nm)))
        # по годам: точечный вклад
        ys = []
        for y, gy in g.groupby("year"):
            zy = z[gy.index.values]
            if zy.sum() >= 8 and gy.coin[zy].nunique() >= 5 and (~zy).sum() >= 8:
                m1, m0 = gy.net[zy].mean(), gy.net[~zy].mean()
                ys.append((int(y), round((m0 - m1) * zy.mean(), 3)))
        rows[-1]["by_year"] = ";".join(f"{y}:{v}" for y, v in ys)
        rows[-1]["years_same_sign"] = (len(ys) >= 2 and (all(v > 0 for _, v in ys) or all(v < 0 for _, v in ys)))
        rows[-1]["years_n"] = len(ys)
    # C9 — описательно, тип выхода
    for rs, gr in g.groupby("reason"):
        c9.append(dict(obj=obj, reason=rs, n=len(gr), share=round(len(gr) / len(g), 4), mean_net=round(gr.net.mean(), 3),
                       mean_gross=round(gr.gross.mean(), 3), contrib_to_mean=round(gr.net.sum() / len(g), 3)))
o = pd.DataFrame(rows)
# Холм по K=222
t = o[o.p.notna()].sort_values("p").reset_index()
adj = np.minimum(1, np.maximum.accumulate([(K - i) * p for i, p in enumerate(t.p)]))
o["p_holm"] = np.nan; o.loc[t["index"], "p_holm"] = adj
def verdict(x):
    if isinstance(x.verdict, str): return x.verdict
    if x.p_holm < 0.05 and x.contrib > 0 and x.years_n >= 2 and x.years_same_sign: return "объясняет (предв., без контроля)"
    if x.p_holm < 0.05 and x.contrib < 0 and x.years_n >= 2 and x.years_same_sign: return "не объясняет (лучше остальных)"
    return "не различимо"
if "verdict" not in o: o["verdict"] = np.nan
o["verdict"] = o.apply(verdict, axis=1)
o = o.drop(columns=["idx"], errors="ignore")
o.to_csv(out, index=False)
if out9: pd.DataFrame(c9).to_csv(out9, index=False)
pd.set_option("display.width", 250); pd.set_option("display.max_colwidth", 40)
print(o[["obj", "fam", "row", "n1", "share", "mean_z1", "mean_z0", "contrib", "lo", "hi", "p_holm", "years_n", "years_same_sign", "verdict"]].to_string())
print(pd.DataFrame(c9).to_string())
print("cross-vs-fund!=0:", float(((r.fund != 0) == r.crossed).mean()))
