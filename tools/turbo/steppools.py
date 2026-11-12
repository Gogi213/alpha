#!/usr/bin/env python3
"""G-022 turbo (`steps.sh`): pool-N.json = the N most liquid symbols of pool-full.json by quoteVolume24h rank (/data/turbo-probe/l1/symbols.json). Usage: steppools.py <step dir>"""
import json
import sys

d = sys.argv[1]
pool = json.load(open(d + "/pool-full.json"))["pool"]
rank = {s: i for i, s in enumerate(json.load(open("/data/turbo-probe/l1/symbols.json")))}
pool = sorted(pool, key=lambda s: rank.get(s, 10**6))
for n in (10, 20, 40, 80, 160, 320):
    json.dump({"pool": pool[:n]}, open(f"{d}/pool-{n}.json", "w"))
