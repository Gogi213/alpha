#!/usr/bin/env python3
"""TK-373: агрегат tk373-bn2.py: net ± SE (кластеры монето-сутки), p_rand (знаковая перестановка), BH по всем строкам all; срезы dex/nodex и сутки.
usage: tk373-bn2agg.py <out-dir> <out-prefix>"""
import sys, os, glob, gzip, csv
import numpy as np
D, PFX = sys.argv[1:3]
rng = np.random.default_rng(3732); NP = 4000
NAMES = ["by10", "by60", "by300", "bn10", "bn60", "bn300", "bypre", "bnpre", "dpx10", "dpx60", "dpx300", "imb60"]
rows = []
for f in sorted(glob.glob(f"{D}/coin/*.tsv.gz")):
    coin = os.path.basename(f)[:-7]
    for ln in gzip.open(f, "rt"):
        a = ln.rstrip("\n").split("\t")
        if len(a) < 28: continue
        rows.append((a[0], coin, int(a[1])) + tuple(float(x) if x else np.nan for x in a[4:28]))
hid = np.array([r[0] for r in rows]); coin = np.array([r[1] for r in rows]); day = np.array([r[2] for r in rows])
V = np.array([r[3:] for r in rows]); col = {n: V[:, 2 * i + 1] for i, n in enumerate(NAMES)}
hasdex = ~np.isnan(col["dpx60"]); g = np.char.add(np.char.add(coin, "|"), day.astype(str))
def test(y, gk):
    ok = ~np.isnan(y); y = y[ok]; gg = np.unique(gk[ok], return_inverse=True)[1]; n = len(y)
    if n < 30: return np.nan, np.nan, np.nan, n, 0
    S = np.bincount(gg, weights=y); N = np.bincount(gg).astype(float); m = y.mean(); k = len(S)
    se = np.sqrt(((S - m * N) ** 2).sum()) / n
    p = (1 + (np.abs(rng.choice([-1.0, 1.0], size=(NP, k)) @ S) / n >= abs(m)).sum()) / (NP + 1)
    return m, se, p, n, k
out = []
for h0 in sorted(set(hid)):
    mh = hid == h0
    for tag, m in (("all", mh), ("dex", mh & hasdex), ("nodex", mh & ~hasdex)) + tuple((f"day{d}", mh & (day == d)) for d in range(17, 22)):
        for nm in NAMES:
            if tag == "nodex" and nm.startswith(("dpx", "imb")): continue
            r = test(col[nm][m], g[m]); out.append([tag, h0, nm, r[3], r[4], r[0], r[1], r[2], np.nan])
al = [r for r in out if r[0] == "all" and not np.isnan(r[7])]
ps = np.array([r[7] for r in al]); o = np.argsort(ps); q = np.empty(len(ps)); run = 1.0; mm = len(ps)
for rank, i in reversed(list(enumerate(o, 1))):
    run = min(run, ps[i] * mm / rank); q[i] = run
for r, qq in zip(al, q): r[8] = qq
with open(PFX + ".csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["срез", "событие", "статистика", "n_событий", "кластеров", "net", "se", "p_rand", "q_BH"])
    for r in out: w.writerow([r[0], r[1], r[2], r[3], r[4], f"{r[5]:.4g}", f"{r[6]:.3g}", f"{r[7]:.4g}", f"{r[8]:.3g}"])
with open(PFX + "-top.txt", "w") as f:
    for r in out:
        if r[0] in ("all", "dex"): f.write(f"{r[0]:>6} {r[1]:6} {r[2]:7} n={r[3]:7} k={r[4]:4} net {r[5]:+.3f}±{r[6]:.3f} p={r[7]:.4f} q={r[8]:.3g}\n")
print(open(PFX + "-top.txt").read())
