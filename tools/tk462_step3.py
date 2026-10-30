#!/usr/bin/env python3
"""TK-462 шаг 3 (P-TK462 §3 п.3): совместный МНК по строкам, прошедшим маргинальный вердикт «объясняет (предв.)».
python tk462_step3.py <entries.csv.gz> <feat2c.csv.gz> <out.csv>
Только O2 (у O1 и O3 прошла одна строка/ни одной — совместная модель = маргинальная). z1 = доля комиссий/риск в средней|высокой
терцили (q по всем сделкам O2, как шаг 2а), z2 = тренд BTC L=20 «по тренду» (c5_20*side>0). net ~ 1+z1+z2; SE — кластер-бутстрап
по монетам (B=4000, seed 462); вклад = b*доля(z=1); свободный член = необъяснённый остаток (mean - sum b*доля)."""
import sys
import numpy as np, pandas as pd
MK, TK, B = 1.26, 3.15, 4000
ent, feat, out = sys.argv[1:4]
e = pd.read_csv(ent, usecols=["sym", "tf", "form", "t0", "gross", "n_mk", "fund", "risk"])
e = e[e.form.astype(str).str.upper().str.startswith("B") & e.fund.notna()].copy()
e["fee"] = e.n_mk * MK + (2 - e.n_mk) * TK
e["cr"] = e.fee / e.risk
f = pd.read_csv(feat, usecols=["coin", "obj", "tf", "t_in", "side", "net", "c5_20", "year"])
f = f[f.obj == "O2"]
e["net"] = (e.gross - e.fee - e.fund).round(6); f = f.assign(net=f.net.round(6))
for d_, tcol, cc in ((e, "t0", "sym"), (f, "t_in", "coin")):
    d_["_k"] = d_.groupby([cc, "tf", tcol, "net"]).cumcount()
m = f.merge(e[["sym", "tf", "t0", "net", "_k", "cr"]], left_on=["coin", "tf", "t_in", "net", "_k"], right_on=["sym", "tf", "t0", "net", "_k"], how="inner")
print("O2 feat", len(f), "entries", len(e), "joined", len(m))
m = m[m.cr.notna() & m.c5_20.notna()].copy()
q = np.nanquantile(m.cr, [1 / 3, 2 / 3])
m["z1"] = (m.cr > q[0]).astype(float)
m["z2"] = ((m.c5_20 * m.side) > 0).astype(float)
print("corr z1,z2", m.z1.corr(m.z2), "share", m.z1.mean(), m.z2.mean(), "q", q)
cg = m.assign(one=1.0).groupby("coin")
XtX = {}; Xty = {}; S = {}
for c, g in cg:
    Xc = np.column_stack([np.ones(len(g)), g.z1.values, g.z2.values]); XtX[c] = Xc.T @ Xc; Xty[c] = Xc.T @ g.net.values; S[c] = (len(g), g.net.sum())
coins = np.array(list(XtX)); A = np.array([XtX[c] for c in coins]); Bv = np.array([Xty[c] for c in coins]); N = np.array([S[c][0] for c in coins], float); Sy = np.array([S[c][1] for c in coins])
def fit(w):
    G = np.tensordot(w, A, 1); h = w @ Bv; b = np.linalg.solve(G, h); n = w @ N
    sh = G[0, 1:] / n
    return np.r_[b, b[1:] * sh, (w @ Sy) / n - (b[1:] * sh).sum()]
pt = fit(np.ones(len(coins)))
rng = np.random.default_rng(462)
bs = np.array([fit(rng.multinomial(len(coins), np.ones(len(coins)) / len(coins)).astype(float)) for _ in range(B)])
lo, hi = np.quantile(bs, .025, 0), np.quantile(bs, .975, 0)
names = ["b0", "b_z1", "b_z2", "contrib_z1", "contrib_z2", "residual"]
yrs = {}
for yr, g in m.groupby("year"):
    yrs[yr] = np.linalg.lstsq(np.column_stack([np.ones(len(g)), g.z1, g.z2]), g.net.values, rcond=None)[0]
rows = [dict(term=n, est=pt[i], lo=lo[i], hi=hi[i]) for i, n in enumerate(names)]
rows.append(dict(term="mean_net", est=m.net.mean(), lo=np.nan, hi=np.nan))
rows.append(dict(term="by_year_b_z1", est=np.nan, lo=np.nan, hi=np.nan, note=";".join(f"{k}:{v[1]:.2f}" for k, v in yrs.items())))
rows.append(dict(term="by_year_b_z2", est=np.nan, lo=np.nan, hi=np.nan, note=";".join(f"{k}:{v[2]:.2f}" for k, v in yrs.items())))
d = pd.DataFrame(rows); d.to_csv(out, index=False); print(d.to_string())
