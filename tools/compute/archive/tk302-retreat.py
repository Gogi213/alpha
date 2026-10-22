#!/usr/bin/env python3
"""TK-302: печать П-15:49 для Г-102 — n_retreat_window_entries и заполнения по формам retreat<k>t<T>, половина А, янв–сен (forms.csv прогонов L-P15F2 и L-P15F2B; движок не гонялся).
  python tk302-retreat.py <out.csv>"""
import csv, glob, hashlib, collections, sys
isA = lambda s: int(hashlib.sha256(s.encode()).hexdigest(), 16) % 2 == 0
agg = collections.defaultdict(lambda: [0, 0, 0])
for root in ("/data/launch/out/L-P15F2", "/data/launch/out/L-P15F2B"):
    for f in glob.glob(root + "/out/run-*/out/*/*/forms.csv"):
        ls = [l for l in open(f, encoding="utf-8") if not l.startswith("#")]
        for r in csv.DictReader(ls):
            if not r["form"].startswith("retreat") or not isA(r["symbol"]): continue
            a = agg[r["form"]]; a[0] += int(r["n_fills"]); a[1] += int(r["n_retreat_window_entries"] or 0); a[2] += int(r["n_signals"])
w = csv.writer(open(sys.argv[1], "w", newline=""))
w.writerow(["form", "n_fills_A", "n_retreat_window_entries_A", "n_signals_A"])
for k in sorted(agg): w.writerow([k] + agg[k])
print(len(agg))
