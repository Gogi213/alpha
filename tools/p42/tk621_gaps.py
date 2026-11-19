#!/usr/bin/env python3
"""TK-621: internal gaps in the published S3 list (days between a symbol's first and last published day with no file), per kind.
python3 tk621_gaps.py <list_dir> -> prints a summary and writes gaps.tsv (kind, symbol, day)."""
import collections
import datetime as D
import json
import sys

L = sys.argv[1]
C = json.load(open(L + "/contracts.json"))["contracts"]
have = collections.defaultdict(set)
for l in open(L + "/files.tsv"):
    k, s, d, *_ = l.rstrip("\n").split("\t")
    have[(k, s)].add(d)
D1 = D.date(2026, 11, 13)
gaps = []
for s, c in sorted(C.items()):
    for k in ("aggTrades", "klines"):
        days = sorted(have[(k, s)])
        if not days:
            gaps.append((k, s, "ALL"))
            continue
        a, b = D.date.fromisoformat(days[0]), D.date.fromisoformat(days[-1])
        # a contract trading up to the end must have the last day D1 (S3 publishes D1 for live contracts)
        if c["last_day"] == "2026-11-13":
            b = D1
        n = (b - a).days + 1
        for i in range(n):
            d = (a + D.timedelta(i)).isoformat()
            if d not in have[(k, s)]:
                gaps.append((k, s, d))
cnt = collections.Counter((g[0], g[1]) for g in gaps)
print("gap files:", len(gaps), collections.Counter(g[0] for g in gaps))
print("symbols with gaps:", len({g[1] for g in gaps}))
for (k, s), n in cnt.most_common(25):
    print(k, s, n, [g[2] for g in gaps if g[0] == k and g[1] == s][:6])
with open(L + "/gaps.tsv", "w") as f:
    for g in gaps:
        f.write("\t".join(g) + "\n")
