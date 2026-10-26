#!/usr/bin/env python3
"""TK-415: охват OI/фандинга по cov/*.tsv → csv (биржа,вид,инструмент,строк,первая_мс,последняя_мс,запросов,примечание)
+ сводка по (биржа, вид): инструментов, с данными, полный месяц (OI 8640 строк при 5 мин), строк, размер.
Использование: tk415-finalize.py <root> <выход.csv>
"""
import glob
import os
import sys
from collections import defaultdict

root, out = sys.argv[1], sys.argv[2]
rows = []
for f in sorted(glob.glob(root + "/cov/*.tsv")):
    ex = os.path.basename(f).split("-")[0]
    kind, sym, n, first, last, nreq, note = open(f, encoding="utf-8").read().rstrip("\n").split("\t")
    rows.append((ex, kind, sym, int(n), first, last, nreq, note))
with open(out, "w", encoding="utf-8", newline="") as g:
    g.write("биржа,вид,инструмент,строк,первая_мс,последняя_мс,запросов,примечание\n")
    for r in rows:
        g.write(",".join(str(x) for x in r) + "\n")
agg = defaultdict(lambda: [0, 0, 0, 0, 0])
for ex, kind, sym, n, first, last, nreq, note in rows:
    a = agg[(ex, kind)]
    a[0] += 1
    a[1] += n > 0
    a[2] += (n >= 8640) if kind == "oi" else 0
    a[3] += n
    a[4] += bool(note)
for (ex, kind), a in sorted(agg.items()):
    sz = sum(os.path.getsize(p) for p in glob.glob("%s/%s/%s/*.csv.gz" % (root, ex, kind)))
    print("%s %s: инструментов %d, с данными %d, полный месяц(OI) %d, строк %d, с примечанием %d, %.1f МБ" % (ex, kind, a[0], a[1], a[2], a[3], a[4], sz / 1e6))
