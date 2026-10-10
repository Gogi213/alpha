#!/usr/bin/env python3
"""Свод validate по монетам (TK-037/TK-038): <validate-out>/*/hours.csv -> CSV по монете + итог.
Использование: tk038-bycoin.py <validate-out> <out.csv>"""
import csv, sys, glob, collections
src, out = sys.argv[1], sys.argv[2]
agg = collections.defaultdict(collections.Counter)
dirs = set()
for p in sorted(glob.glob(f"{src}/*/hours.csv")):
    dirs.add(p.rsplit("/", 2)[-2])
    with open(p, newline="") as f:
        for r in csv.DictReader(f):
            c = agg[r["symbol"]]
            c["hours"] += 1
            c["trades"] += int(r["trades"])
            for k in ("trades_out_of_range", "trades_violations", "snapshot_drift", "snapshot_drift_levels", "dup_groups", "backward_steps"):
                c[k] += int(r[k])
            c["hours_out_of_range"] += int(int(r["trades_out_of_range"]) > 0)
            c["hours_violations"] += int(int(r["trades_violations"]) > 0)
            c["hours_drift"] += int(int(r["snapshot_drift"]) > 0)
            c["hours_dup"] += int(int(r["dup_groups"]) > 0)
            c["not_ok"] += int(r["status"] != "ok")
cols = ["trades", "trades_out_of_range", "trades_violations", "snapshot_drift", "snapshot_drift_levels", "dup_groups",
        "backward_steps", "hours_out_of_range", "hours_violations", "hours_drift", "hours_dup"]
with open(out, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["symbol"] + cols)
    for s in sorted(agg):
        w.writerow([s] + [agg[s][k] for k in cols])
t = collections.Counter()
for c in agg.values():
    t.update(c)
print("dirs", len(dirs), "coins", len(agg), "coin_hours", t["hours"], "not_ok", t["not_ok"])
for k in cols:
    print(k, t[k], "coins>0:", sum(1 for c in agg.values() if c[k] > 0))
