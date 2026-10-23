#!/usr/bin/env python3
"""TK-373: добавка к tk373-dir.py для tk373-bn.py: bypre (ход Bybit до t0 по стороне события) и bn10/60/300 (Binance дальше), net ± SE по кластерам монето-сутки,
p_rand = знаковая перестановка кластерных сумм; плюс срезы по DEX/без DEX и по суткам. usage: tk373-bnagg.py <bn-out-dir> <out-prefix>"""
import sys, os, glob, gzip, csv
import numpy as np
D, PFX = sys.argv[1:3]
rng = np.random.default_rng(3731); NP = 4000
rows = []
for f in sorted(glob.glob(f"{D}/coin/*.tsv.gz")):
    coin = os.path.basename(f)[:-7]
    for ln in gzip.open(f, "rt"):
        a = ln.rstrip("\n").split("\t")
        if len(a) < 32: continue
        rows.append((a[0], coin, int(a[1]), float(a[3]), a[4] != "" and a[6] != "") + tuple(float(x) if x else np.nan for x in a[4:32]))
hid = np.array([r[0] for r in rows]); coin = np.array([r[1] for r in rows]); day = np.array([r[2] for r in rows])
V = np.array([r[5:] for r in rows]); hasdex = ~np.isnan(V[:, 4])
cols = {"bypre": V[:, 21], "bn10": V[:, 23], "bn60": V[:, 25], "bn300": V[:, 27]}
def test(y, g):
    ok = ~np.isnan(y); y = y[ok]; gg = np.unique(g[ok], return_inverse=True)[1]; n = len(y)
    if n < 30: return np.nan, np.nan, np.nan, n, 0
    S = np.bincount(gg, weights=y); N = np.bincount(gg).astype(float); m = y.mean(); k = len(S)
    se = np.sqrt(((S - m * N) ** 2).sum()) / n
    p = (1 + (np.abs(rng.choice([-1.0, 1.0], size=(NP, k)) @ S) / n >= abs(m)).sum()) / (NP + 1)
    return m, se, p, n, k
g = coin + "|" + day.astype(str)
with open(PFX + ".csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["срез", "окно", "статистика", "n_событий", "кластеров", "net", "se", "p_rand"])
    for h0 in ("R10", "R60"):
        for tag, m in (("all", hid == h0), ("dex", (hid == h0) & hasdex), ("nodex", (hid == h0) & ~hasdex)) + tuple((f"day{d}", (hid == h0) & (day == d)) for d in range(17, 22)):
            for nm, y in cols.items():
                r = test(y[m], g[m]); w.writerow([tag, h0, nm, r[3], r[4], f"{r[0]:.4g}", f"{r[1]:.3g}", f"{r[2]:.4g}"])
print(open(PFX + ".csv").read())
