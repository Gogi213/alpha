#!/usr/bin/env python3
"""TK-084: свод П-12 по XAU/CL — символ × месяц × клетка: суток, сигналов, подач, филлов, Σ net bps. Читает /data/tk084/wx-<сутки>/b5/p12/<сутки>/*/forms.csv, пишет CSV в argv[1]."""
import csv, glob, sys, collections
acc = collections.defaultdict(lambda: [set(), 0, 0, 0, 0.0])
for f in sorted(glob.glob("/data/tk084/wx-2*/b5/p12/*/*/forms.csv")):
    with open(f, newline="") as fh:
        rows = [l for l in fh if not l.startswith("#")]
    for r in csv.DictReader(rows):
        a = acc[(r["symbol"], r["day_utc"][:7], r["form"])]
        a[0].add(r["day_utc"]); a[1] += int(r["n_signals"]); a[2] += int(r["n_submitted"]); a[3] += int(r["n_fills"]); a[4] += float(r["sum_net_bps"])
with open(sys.argv[1], "w", newline="") as o:
    w = csv.writer(o); w.writerow(["symbol", "month", "cell", "days", "n_signals", "n_submitted", "n_fills", "sum_net_bps"])
    for k in sorted(acc):
        a = acc[k]; w.writerow([*k, len(a[0]), a[1], a[2], a[3], f"{a[4]:.2f}"])
