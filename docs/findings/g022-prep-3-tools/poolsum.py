#!/usr/bin/env python3
"""Сводка пула: poolsum.py pool-DAY.json [поле-признак-в-meta]  — размер, время снимка, топ-12, крайние, медиана, доля топ-10, число ниже 330 тыс."""
import collections, datetime, json, statistics, sys

p = json.load(open(sys.argv[1]))
q = p["qv"]
ks = list(q)
print("snapshot UTC", datetime.datetime.fromtimestamp(p["ts_ms"] / 1000, datetime.timezone.utc).isoformat(), "pool", len(p["pool"]), "source", p["source"][:90])
print({k: p[k] for k in p if k.startswith("n_")})
print("top12", [(k, round(q[k] / 1e6, 2)) for k in ks[:12]])
print("last3", [(k, round(q[k])) for k in ks[-3:]])
print("median", round(statistics.median(q.values())), "sum", round(sum(q.values())), "top10 share", round(sum(q[k] for k in ks[:10]) / sum(q.values()), 3), "near-threshold(<330k)", sum(1 for k in q if q[k] < 330000))
if len(sys.argv) > 2:
    f = sys.argv[2]
    print(f, collections.Counter(str(p["meta"][k].get(f)) for k in p["pool"]))
