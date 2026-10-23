#!/usr/bin/env python3
"""TK-373: агрегация tk373-liqtest.py: по (цепь|все, класс add/rem, q) и горизонту: среднее M и net ± SE (кластеры = монето-сутки), n событий.
usage: tk373-liqagg.py <events.tsv.gz> <out-prefix>"""
import sys, gzip, csv
import numpy as np
F, PFX = sys.argv[1:3]
R = list(csv.DictReader(gzip.open(F, "rt"), delimiter="\t"))
cols = ["pre_M", "pre_net", "b10_M", "b10_net", "b60_M", "b60_net", "b300_M", "b300_net"]
def ms(y, g):
    ok = ~np.isnan(y); y = y[ok]; g = np.unique(g[ok], return_inverse=True)[1]; n = len(y)
    if n < 30: return np.nan, np.nan, n, 0
    S = np.bincount(g, weights=y); N = np.bincount(g).astype(float); m = y.mean()
    return m, np.sqrt(((S - m * N) ** 2).sum()) / n, n, len(N)
out = []
for ch in ("all", "ethereum", "base"):
    for cls in ("add", "rem"):
        for q in ("0.95", "0.99"):
            S = [r for r in R if r["cls"] == cls and r["q"] == q and (ch == "all" or r["chain"] == ch)]
            if not S: continue
            cl = np.array([r["coin"] + "|" + r["day"] for r in S])
            row = [ch, cls, q]
            for c in cols:
                y = np.array([float(r[c]) if r[c] else np.nan for r in S])
                m, se, n, k = ms(y, cl)
                row += [f"{m:+.3f}±{se:.3f}" if m == m else "-"]
            row += [len(S), len(set(cl))]
            out.append(row)
with open(PFX + ".csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["chain", "cls", "q"] + cols + ["events", "coin_days"]); w.writerows(out)
for r in out: print(*r, sep="\t")
