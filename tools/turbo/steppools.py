#!/usr/bin/env python3
"""G-022 turbo (`steps.sh`): pool-N.json = the N most liquid symbols of pool-full.json by 24h volume. Usage: steppools.py <step dir>
Ranking: `qv` of pool-full.json when present (OKX and any exchange whose pool file carries volumes); otherwise Binance quoteVolume rank (/data/turbo-probe/l1/symbols.json).
`meta`/`qv` of the chosen symbols are carried into every pool-N.json (the collector copies them into its own pool-<day>.json)."""
import json
import sys

d = sys.argv[1]
full = json.load(open(d + "/pool-full.json"))
pool = full["pool"]
qv = full.get("qv")
if qv:
    pool = sorted(pool, key=lambda s: -qv.get(s, 0))
else:
    rank = {s: i for i, s in enumerate(json.load(open("/data/turbo-probe/l1/symbols.json")))}
    pool = sorted(pool, key=lambda s: rank.get(s, 10**6))
for n in (10, 20, 40, 80, 160, 320):
    sub = {"pool": pool[:n]}
    for k in ("meta", "qv"):
        if k in full:
            sub[k] = {s: full[k][s] for s in sub["pool"] if s in full[k]}
    json.dump(sub, open(f"{d}/pool-{n}.json", "w"))
