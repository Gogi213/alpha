"""Chart vs table: does the chart's max excursion from the centre line reach the table's max peak? Read-only."""
import json

import numpy as np

D = "/tmp/arbreview"
doc = json.load(open(f"{D}/arb.json"))
f = open(f"/data/arb-tab/live/{doc['chart']}", "rb")
d3, under, over, n, nulls = [], 0, 0, 0, 0
for r in doc["rows"]:
    if not r["pk"]:
        continue
    f.seek(r["o"])
    c = json.loads(f.read(r["l"]))
    S, B, C = (np.array([np.nan if v is None else v / 10 for v in c[k]]) for k in "SBC")
    ex = np.nanmax(np.r_[S - C, C - B])
    d = ex - r["pk"][2]
    d3.append(d)
    n += 1
    nulls += int(np.isnan(S).sum())
    if d < -3:
        under += 1
    if d > 3:
        over += 1
d3 = np.array(d3)
print(f"rows {n}: chart max excursion − table max peak, bps: p1 {np.percentile(d3, 1):.1f} p50 {np.median(d3):.1f} p99 {np.percentile(d3, 99):.1f}; chart lower by >3 bps: {under}; higher by >3: {over}; null slots total {nulls}")
