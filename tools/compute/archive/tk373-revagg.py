#!/usr/bin/env python3
"""TK-373: агрегация tk373-rev.py. По окну W (R10/R60) и горизонту h: net (M - контроль t±3600) для imb_pre, imb_post, dpx (DEX) и by (Bybit дальше);
среднее ± SE (кластеры = монето-сутки), n. Читать: imb_pre>0 — DEX уже шёл по стороне хода Bybit ДО него (DEX ведёт); imb_post>0 / dpx>0 — DEX идёт ПОСЛЕ (Bybit ведёт).
usage: tk373-revagg.py <dir> <out-prefix>"""
import sys, os, glob, gzip, csv
import numpy as np
D, PFX = sys.argv[1:3]
rows = []
for f in sorted(glob.glob(f"{D}/coin/*.tsv.gz")):
    coin = os.path.basename(f)[:-7]
    for ln in gzip.open(f, "rt"):
        a = ln.rstrip("\n").split("\t")
        if len(a) < 24: continue
        rows.append((a[0], coin, a[1]) + tuple(float(x) if x else np.nan for x in a[4:24]))
hid = np.array([r[0] for r in rows]); cd = np.array([r[1] + "|" + r[2] for r in rows])
cid = np.unique(cd, return_inverse=True)[1]
V = np.array([r[3:] for r in rows])      # [pre_M, pre_net, (post_M, post_net, dpx_M, dpx_net, by_M, by_net) x3 h]  -> 2 + ... проверка ниже
# порядок stats: imb_pre, then per h: imb_post, dpx, by ; каждое — пара (M, net)
names = ["imb_pre"] + [f"{n}{h}" for h in (10, 60, 300) for n in ("imb", "dpx", "by")]
def mean_se(y, g):
    ok = ~np.isnan(y); y = y[ok]; g = np.unique(g[ok], return_inverse=True)[1]; n = len(y)
    if n < 30: return np.nan, np.nan, n
    S = np.bincount(g, weights=y); N = np.bincount(g).astype(float); m = y.mean()
    return m, np.sqrt(((S - m * N) ** 2).sum()) / n, n
out = []
for h0 in sorted(set(hid)):
    m0 = hid == h0
    for i, nm in enumerate(names):
        for kind, col in (("M", 2 * i), ("net", 2 * i + 1)):
            m, se, n = mean_se(V[m0, col], cid[m0])
            out.append([h0, nm, kind, n, m, se])
with open(PFX + ".csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["hid", "stat", "kind", "n", "mean", "se"])
    for r in out: w.writerow([r[0], r[1], r[2], r[3], f"{r[4]:.4g}", f"{r[5]:.3g}"])
with open(PFX + "-top.txt", "w") as f:
    for h0 in sorted(set(hid)):
        f.write(f"== {h0}: событий {int((hid == h0).sum())}, монето-суток {len(set(cd[hid == h0]))}\n")
        for nm in names:
            a = [r for r in out if r[0] == h0 and r[1] == nm]
            f.write(f"{nm:9} M {a[0][4]:+.4f}±{a[0][5]:.4f} (n={a[0][3]}) | net {a[1][4]:+.4f}±{a[1][5]:.4f}\n")
print(open(PFX + "-top.txt").read())
