#!/usr/bin/env python3
"""TK-373: направление Bybit→DEX — контроли и «догон» как альфа. Вход: out4/coin/*.tsv.gz от tk373-rev.py.
Колонки: hid,day,s,strength, затем пары (M,net) для imb_pre, и по h=10/60/300: imb,dpx,by.
Тесты: net ± SE (кластеры монето-сутки), p_rand = знаковая перестановка кластерных сумм (N_PERM), BH по всем строкам,
по суткам 16–21, по монетам (доля >0), по силе события, по сети; прирост dpx между горизонтами (вход с задержкой).
usage: tk373-dir.py <rev-out-dir> <usd-root> <out-prefix>"""
import sys, os, glob, gzip, csv, json
import numpy as np
D, USD, PFX = sys.argv[1:4]
NP = 4000
rng = np.random.default_rng(373)
rows = []
for f in sorted(glob.glob(f"{D}/coin/*.tsv.gz")):
    coin = os.path.basename(f)[:-7]
    for ln in gzip.open(f, "rt"):
        a = ln.rstrip("\n").split("\t")
        if len(a) < 24: continue
        rows.append((a[0], coin, int(a[1]), float(a[3])) + tuple(float(x) if x else np.nan for x in a[4:24]))
hid = np.array([r[0] for r in rows]); coin = np.array([r[1] for r in rows]); day = np.array([r[2] for r in rows]); st = np.array([r[3] for r in rows])
V = np.array([r[4:] for r in rows])
net = lambda i: V[:, 2 * i + 1]
names = ["imb_pre"] + [f"{n}{h}" for h in (10, 60, 300) for n in ("imb", "dpx", "by")]
col = {n: net(i) for i, n in enumerate(names)}
col["dpx10_60"] = col["dpx60"] - col["dpx10"]; col["dpx60_300"] = col["dpx300"] - col["dpx60"]
col["by10_60"] = col["by60"] - col["by10"]; col["by60_300"] = col["by300"] - col["by60"]
chains = {}
for ch in ("ethereum", "bsc", "base"):
    for d in os.listdir(f"{USD}/{ch}"): chains.setdefault(d.partition("-")[0], set()).add(ch)
cn = np.array([",".join(sorted(chains.get(c, {"?"}))) for c in coin])

def test(y, g):
    ok = ~np.isnan(y); y = y[ok]; gg = np.unique(g[ok], return_inverse=True)[1]; n = len(y)
    if n < 30: return np.nan, np.nan, np.nan, n, 0
    S = np.bincount(gg, weights=y); N = np.bincount(gg).astype(float); m = y.mean(); k = len(S)
    se = np.sqrt(((S - m * N) ** 2).sum()) / n
    sg = rng.choice([-1.0, 1.0], size=(NP, k))
    perm = np.abs(sg @ S) / n
    p = (1 + (perm >= abs(m)).sum()) / (NP + 1)
    return m, se, p, n, k
out = []
def add(tag, hd, nm, mask):
    m, se, p, n, k = test(col[nm][mask], (coin + "|" + day.astype(str))[mask])
    out.append([tag, hd, nm, n, k, m, se, p])
for h0 in ("R10", "R60"):
    mh = hid == h0
    for nm in col: add("all", h0, nm, mh)
    for nm in ("dpx60", "dpx300", "imb60"):
        for d in range(16, 22): add(f"day{d}", h0, nm, mh & (day == d))
        for lo, hi in ((1, 1.5), (1.5, 2), (2, 3), (3, 99)): add(f"str{lo}-{hi}", h0, nm, mh & (st >= lo) & (st < hi))
        for c in sorted(set(cn[mh])): add(f"chain:{c}", h0, nm, mh & (cn == c))
        for sd in (-1, 1):
            add(f"side{sd:+d}", h0, nm, mh & (V[:, 0] == V[:, 0]) & (np.array([r[0] for r in rows]) == h0))  # placeholder, replaced below
# сторона события: s берём из файла
S = np.array([int(r[0] == r[0]) for r in rows])
# по монетам: доля монет с net>0 и медиана
cois = []
for h0 in ("R10", "R60"):
    mh = hid == h0
    for nm in ("dpx60", "dpx300"):
        cm = []
        for c in sorted(set(coin[mh])):
            y = col[nm][mh & (coin == c)]; y = y[~np.isnan(y)]
            if len(y) >= 20: cm.append((c, len(y), y.mean()))
        v = np.array([x[2] for x in cm])
        cois.append([h0, nm, len(cm), (v > 0).mean(), np.median(v), np.mean([x[1] for x in cm])])
# BH по строкам tag==all
al = [r for r in out if r[0] == "all" and not np.isnan(r[7])]
ps = np.array([r[7] for r in al]); o = np.argsort(ps); q = np.empty(len(ps)); mm = len(ps)
run = 1.0
for rank, i in reversed(list(enumerate(o, 1))):
    run = min(run, ps[i] * mm / rank); q[i] = run
for r, qq in zip(al, q): r.append(qq)
for r in out:
    if len(r) == 8: r.append(np.nan)
out = [r for r in out if not r[0].startswith("side")]
with open(PFX + ".csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["срез", "окно", "статистика", "n_событий", "кластеров", "net", "se", "p_rand", "q_BH"])
    for r in out: w.writerow([r[0], r[1], r[2], r[3], r[4], f"{r[5]:.4g}", f"{r[6]:.3g}", f"{r[7]:.4g}", f"{r[8]:.3g}"])
with open(PFX + "-coins.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["окно", "статистика", "монет", "доля_монет_net>0", "медиана_по_монетам", "ср_событий_на_монету"])
    for r in cois: w.writerow([r[0], r[1], r[2], f"{r[3]:.3f}", f"{r[4]:.3f}", f"{r[5]:.0f}"])
with open(PFX + "-top.txt", "w") as f:
    for r in out:
        f.write(f"{r[0]:>12} {r[1]} {r[2]:10} n={r[3]:6} k={r[4]:4} net {r[5]:+.3f}±{r[6]:.3f} p={r[7]:.4f} q={r[8]:.3g}\n")
    for r in cois: f.write(f"coins {r}\n")
print(open(PFX + "-top.txt").read())
