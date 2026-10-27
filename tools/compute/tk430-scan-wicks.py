#!/usr/bin/env python3
"""TK-430: охват минут-артефактов (h/l далеко от o/c) в свечах 1 м по каталогу контрактов.
Минута-кандидат: l < 0.5·min(o,c) или h > 2·max(o,c) (ход ≥ ×2 внутри минуты при o/c рядом — не рыночный фитиль).
Использование: tk430-scan-wicks.py <каталог со *.csv.gz> <выход.tsv>; выход: минута<TAB>активных контрактов<TAB>кандидатов.
Печатает: контрактов, минут всего, кандидатов всего, топ-10 минут по числу кандидатов.
"""
import gzip
import os
import sys
from collections import defaultdict

src, dst = sys.argv[1], sys.argv[2]
act = defaultdict(int)
bad = defaultdict(int)
nfiles = 0
for fn in sorted(os.listdir(src)):
    if not fn.endswith(".csv.gz"):
        continue
    nfiles += 1
    with gzip.open(os.path.join(src, fn), "rt") as f:
        next(f)
        for line in f:
            p = line.split(",")
            t = int(p[0])
            o, h, l, c = float(p[1]), float(p[2]), float(p[3]), float(p[4])
            act[t] += 1
            if l < 0.5 * min(o, c) or h > 2.0 * max(o, c):
                bad[t] += 1
with open(dst, "w") as g:
    for t in sorted(act):
        g.write("%d\t%d\t%d\n" % (t, act[t], bad.get(t, 0)))
top = sorted(bad.items(), key=lambda kv: -kv[1])[:10]
print("contracts", nfiles, "minutes", len(act), "cand", sum(bad.values()), "min_with_cand", len(bad))
for t, n in top:
    print(t, n, act[t], "%.3f" % (n / act[t]))
