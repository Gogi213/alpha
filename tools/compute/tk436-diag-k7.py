#!/usr/bin/env python3
"""TK-436: разбор расхождения K7 события <id>: close/high из сделок (как tk433-trades.py) против куба свечей.
  tk436-diag-k7.py <end-start-th5.csv> <корень /data/tk417> <куб> <id>"""
import csv, gzip, os, sys
import numpy as np

src, root, cube, eid = sys.argv[1:5]
ev = [r for r in csv.DictReader(open(src, encoding="utf-8")) if r["id"] == eid and r["биржа"] == "bybit" and r["рынок"] == "linear"]
r = ev[0]
sym, p, s = r["инструмент"], int(r["peak_min"]), int(r["знак"])
print("sym", sym, "p", p, "s", s, "строк события", len(ev))
need = set(range(p - 60, p + 61))
d = f"{root}/bybit-linear/{sym}"
A = []
for fn in sorted(os.listdir(d)):
    with gzip.open(f"{d}/{fn}", "rt") as f:
        f.readline()
        for ln in f:
            q = ln.split(",", 10)
            t = float(q[0])
            if int(t // 60) in need:
                A.extend((t, float(q[4])))
A = np.array(A).reshape(-1, 2)
print("сделок", len(A), "отсортированы по t:", bool((np.diff(A[:, 0]) >= 0).all()))
csyms = open(f"{cube}/bybit-linear.syms").read().split()
clo = int(open(f"{cube}/bybit-linear.lo").read())
cmm = np.load(f"{cube}/bybit-linear.npy", mmap_mode="r")
i = csyms.index(sym)
ch = open(f"{cube}/bybit-linear.chs").read().split() if os.path.exists(f"{cube}/bybit-linear.chs") else None
print("каналы", ch, "форма", cmm.shape)
m = A[(A[:, 0] >= p * 60) & (A[:, 0] < (p + 1) * 60)]
print("пик-минута: max сделок", m[:, 1].max(), "min", m[:, 1].min(), "n", len(m))
last = {}
for t, pr in A[(A[:, 0] >= (p + 1) * 60) & (A[:, 0] < (p + 61) * 60)]:
    last[int(t // 60)] = pr
print("минут со сделками после:", len(last))
for mn in sorted(last):
    cc = [float(cmm[i, c, mn - clo]) for c in range(cmm.shape[1])]
    print(mn - p, "last-trade", last[mn], "куб", cc)
print("пик куб", [float(cmm[i, c, p - clo]) for c in range(cmm.shape[1])])
