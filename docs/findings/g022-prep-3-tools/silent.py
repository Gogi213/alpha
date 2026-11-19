#!/usr/bin/env python3
"""Символы пула без единой записи канала за окно: silent.py EX frames.jsonl.zst CHANNEL pool.json [META-ПОЛЕ] — число, оборот, доля «акций/сырья» среди молчавших, список."""
import collections, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stats

ex, path, chan, poolf = sys.argv[1:5]
field = sys.argv[5] if len(sys.argv) > 5 else None
pool = json.load(open(poolf))
seen = collections.Counter()
hdr, it = stats.lines(path)
for d in it:
    if "f" not in d:
        continue
    for (a, sym, e, key, flag, P) in stats.PARSE[ex](d["f"]):
        if a == chan:
            seen[sym] += 1
silent = [s for s in pool["pool"] if s not in seen]
print(chan, "symbols with data", len(seen), "of", len(pool["pool"]), "silent", len(silent))
if field:
    print(field, "silent:", dict(collections.Counter(str(pool["meta"][s].get(field)) for s in silent)), "| pool:", dict(collections.Counter(str(pool["meta"][s].get(field)) for s in pool["pool"])))
print("silent turnover $ (sorted):", [(s, round(pool["qv"][s])) for s in sorted(silent, key=lambda s: -pool["qv"][s])][:40])
