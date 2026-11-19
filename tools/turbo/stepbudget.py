#!/usr/bin/env python3
"""G-022 turbo (`steps.sh`): connect budget of the next process = min($BUDGET_MAX or 280, 288 - our dials (connected + path_rejected_rtt) of the last 300 s in the earlier windows under <step dir>)."""
import glob
import json
import os
import sys
import time

now = int(time.time() * 1000)
used = 0
for f in glob.glob(sys.argv[1] + "/*/data/events-*.jsonl"):
    if time.time() - os.path.getmtime(f) > 330:
        continue
    for ln in open(f):
        try:
            e = json.loads(ln)
        except ValueError:
            continue
        used += e["ev"] in ("connected", "path_rejected_rtt") and e["ts_ms"] > now - 300000
print(max(0, min(int(os.environ.get("BUDGET_MAX", "280")), 288 - used)))   # BUDGET_MAX: копия рядом с боевым процессом на том же IP (суммарно <= 80 % лимита биржи, п. 13)
