#!/usr/bin/env python3
"""TK-373 / P-25: агрегация tk373-run.py — контроль «ход Bybit до события» для L/F.
usage: tk373-agg.py <dir с coin/*.tsv.gz> <out-prefix>
По (гипотеза, h, окно W предхода 10/60/300): Snet(v=1) всего; по квинтилям pre (пул по гипотезе); страта pre<=0 («Bybit ещё не пошёл по стороне события»);
регрессия Snet = a + b*pre, кластеры = монето-сутки (устойчивая SE): a — Snet при нулевом предходе, b — доля хода, уже сидевшая в предходе.
Только L050/052/054/055 и F-серия; BH не применяется (контроль, а не поиск)."""
import sys, os, glob, gzip, csv
import numpy as np

D, PFX = sys.argv[1:3]
H = [1, 5, 30, 60, 300, 900, 3600]
rows = []
for f in sorted(glob.glob(f"{D}/coin/*.tsv.gz")):
    coin = os.path.basename(f)[:-7]
    for ln in gzip.open(f, "rt"):
        a = ln.rstrip("\n").split("\t")
        if len(a) < 14: continue
        rows.append((a[0], coin, int(a[1])) + tuple(float(x) if x else np.nan for x in a[4:14]))
hid = np.array([r[0] for r in rows]); coin = np.array([r[1] for r in rows]); day = np.array([r[2] for r in rows])
vals = np.array([r[3:] for r in rows])             # pre10, pre60, pre300, sn x7
cid = np.unique(np.char.add(coin, np.char.add("|", day.astype(str))), return_inverse=True)[1]
PW = {10: 0, 60: 1, 300: 2}

def csum(x, g, n):
    return np.bincount(g, weights=x, minlength=n)

def mean_se(y, g):
    n = len(y)
    if n < 30: return np.nan, np.nan, n
    G = g.max() + 1
    S = csum(y, g, G); N = np.bincount(g, minlength=G).astype(float)
    m = y.mean()
    se = np.sqrt(((S - m * N) ** 2).sum()) / n
    return m, se, n

def ols(y, x, g):
    X = np.column_stack([np.ones_like(x), x]); n = len(y)
    XtX = X.T @ X
    if np.linalg.cond(XtX) > 1e12: return None
    inv = np.linalg.inv(XtX); b = inv @ (X.T @ y); e = y - X @ b
    G = g.max() + 1
    U = np.zeros((G, 2)); np.add.at(U, g, X * e[:, None])
    V = inv @ (U.T @ U) @ inv
    return b, np.sqrt(np.diag(V))

out = []
for h0 in sorted(set(hid)):
    if h0[0] not in "LF": continue
    m0 = hid == h0
    for hi, h in enumerate(H):
        sn = vals[:, 3 + hi]
        for W, wi in PW.items():
            pre = vals[:, wi]
            ok = m0 & ~np.isnan(sn) & ~np.isnan(pre)
            if ok.sum() < 200: continue
            y = sn[ok]; x = pre[ok]; g0 = cid[ok]
            g = np.unique(g0, return_inverse=True)[1]
            mA, seA, nA = mean_se(y, g)
            neg = x <= 0
            mN, seN, nN = mean_se(y[neg], g[neg])
            qs = np.quantile(x, [.2, .4, .6, .8])
            qb = np.digitize(x, qs)
            qm = [mean_se(y[qb == k], g[qb == k]) for k in range(5)]
            r = ols(y, x, g)
            a, b, sa, sb = (r[0][0], r[0][1], r[1][0], r[1][1]) if r else (np.nan,) * 4
            out.append([h0, h, W, nA, mA, seA, nN, mN, seN, a, sa, b, sb, x.mean()] + [v for t in qm for v in (t[0], t[1])])
cols = ["hid", "h", "W", "n", "snet", "se", "n_pre_le0", "snet_pre_le0", "se_pre_le0", "a_pre0", "se_a", "b_slope", "se_b", "pre_mean"] + [f"q{k}_{c}" for k in range(1, 6) for c in ("mean", "se")]
with open(PFX + ".csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f); w.writerow(cols)
    for r in out: w.writerow([f"{v:.4g}" if isinstance(v, float) else v for v in r])
with open(PFX + "-top.txt", "w", encoding="utf-8") as f:
    f.write("hid h W | n Snet±se | pre<=0: n Snet±se | a(pre=0)±se b±se | квинтили pre: mean(se)\n")
    for r in out:
        if r[2] != 60 and r[0] not in ("F026",): pass
        f.write(f"{r[0]} h={r[1]:>4} W={r[2]:>3} | {r[3]:>7} {r[4]:+.2f}±{r[5]:.2f} | {r[6]:>7} {r[7]:+.2f}±{r[8]:.2f} | a={r[9]:+.2f}±{r[10]:.2f} b={r[11]:+.3f}±{r[12]:.3f} | "
                + " ".join(f"{r[14 + 2*k]:+.2f}({r[15 + 2*k]:.2f})" for k in range(5)) + "\n")
print(len(out), "строк")
