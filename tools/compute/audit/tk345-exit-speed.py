#!/usr/bin/env python3
"""TK-345: доля быстрых выходов по rounds.csv прошлых прогонов.

Использование: tk345-exit-speed.py <out.csv> <метка>=<glob rounds.csv> ... [--max-files N]
Для каждой (метка, форма): n кругов, доля reason=stop, доля выходов за <= 1 с от t0 (сигнал касания),
доля стопов за <= 1 с, медиана удержания. Флаг: стоп-<=1с > 50 % кругов.
"""
import csv, glob, sys, statistics
from collections import defaultdict

args = sys.argv[1:]
mx = 400
if "--max-files" in args:
    i = args.index("--max-files"); mx = int(args[i + 1]); del args[i:i + 2]
out, specs = args[0], args[1:]
rows = []
for sp in specs:
    lab, pat = sp.split("=", 1)
    fs = sorted(glob.glob(pat))
    if len(fs) > mx:
        step = len(fs) / mx
        fs = [fs[int(k * step)] for k in range(mx)]
    agg = defaultdict(lambda: [0, 0, 0, 0, []])
    for f in fs:
        with open(f, newline="") as fh:
            for r in csv.DictReader(l for l in fh if not l.startswith("#")):
                try:
                    h = (int(r["exit_ns"]) - int(r["t0_ns"])) / 1e9
                except (KeyError, ValueError):
                    continue
                a = agg[r["form"]]
                a[0] += 1
                st = r["reason"] == "stop"
                a[1] += st
                a[2] += h <= 1.0
                a[3] += st and h <= 1.0
                a[4].append(h)
    for form, a in agg.items():
        n = a[0]
        rows.append([lab, form, len(fs), n, a[1], round(a[1] / n, 4), round(a[2] / n, 4), round(a[3] / n, 4),
                     round(statistics.median(a[4]), 2), int(a[3] / n > 0.5)])
with open(out, "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["source", "form", "files", "n", "stop_n", "stop_share", "le1s_share", "stop_le1s_share", "median_hold_s", "flag_stop_le1s_gt50"])
    w.writerows(rows)
print(len(rows), "строк", sum(r[-1] for r in rows), "флагов")
