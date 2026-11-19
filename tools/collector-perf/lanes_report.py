#!/usr/bin/env python3
"""Отчёт по /data/lanes/lanes.jsonl: по биржам и шардам — доставлено каждой линией, доля «первой», число и доля личностей, принесённых только этой линией; приращения складываются по запускам.
usage: lanes_report.py [файл] [--shards]"""
import collections, json, sys

path = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "/data/lanes/lanes.jsonl"
per_shard = collections.defaultdict(lambda: {"first": [0, 0], "delivered": [0, 0], "unique": [0, 0]})
last = {}
t0 = t1 = None
for ln in open(path):
    j = json.loads(ln)
    if "error" in j:
        continue
    t0 = j["t"] if t0 is None else t0
    t1 = j["t"]
    key = (j["ex"], j["shard"])
    prev = last.get(key)
    acc = per_shard[key]
    for k in ("first", "delivered", "unique"):
        cur = j[k]
        base = prev[k] if prev and prev["start"] == j["start"] else [0, 0]
        for i in (0, 1):
            acc[k][i] += max(0, cur[i] - base[i])
    last[key] = j
exs = sorted({k[0] for k in per_shard})
print(f"окно снимков {(t1 - t0) / 3600:.1f} ч" if t0 else "нет данных")
for ex in exs:
    tot = {"first": [0, 0], "delivered": [0, 0], "unique": [0, 0]}
    for (e, sh), v in per_shard.items():
        if e == ex:
            for k in tot:
                for i in (0, 1):
                    tot[k][i] += v[k][i]
    d = max(1, tot["delivered"][0] + tot["delivered"][1])
    print(f"{ex:8s} доставлено {tot['delivered']}, первая {tot['first']} ({100 * tot['first'][0] / max(1, sum(tot['first'])):.0f} % / {100 * tot['first'][1] / max(1, sum(tot['first'])):.0f} %), "
          f"только своя {tot['unique']} = {100 * sum(tot['unique']) / d:.4f} % доставленного")
    if "--shards" in sys.argv:
        for (e, sh), v in sorted(per_shard.items()):
            if e == ex:
                dd = max(1, sum(v["delivered"]))
                print(f"    {sh:8s} доставлено {v['delivered']} первая {v['first']} только своя {v['unique']} ({100 * sum(v['unique']) / dd:.4f} %)")
