#!/usr/bin/env python3
"""TK-621: files.tsv (tk621_list.py) minus what already lies in the TK-599 window trees (wa/wb/wc) -> missing.tsv.
Run on srv under systemd-run: python3 tk621_missing.py <list_dir>"""
import collections
import os
import re
import sys

L = sys.argv[1]
rows = [l.rstrip("\n").split("\t") for l in open(L + "/files.tsv")]
have = {"aggTrades": set(), "klines": set()}
zip_re, csv_re = re.compile(r"(\d{4}-\d{2}-\d{2})\.zip$"), re.compile(r"^(\d{4}-\d{2}-\d{2})\.csv$")
for w in ("wa", "wb", "wc"):
    r = "/data/p42-tk599/" + w
    for s in os.listdir(r + "/aggtrades"):
        for n in os.listdir(r + "/aggtrades/" + s):
            m = zip_re.search(n)
            if m:
                have["aggTrades"].add((s, m.group(1)))
    for s in os.listdir(r + "/klines"):
        for n in os.listdir(r + "/klines/" + s):
            m = csv_re.search(n)
            if m:
                have["klines"].add((s, m.group(1)))
miss = [r for r in rows if (r[1], r[2]) not in have[r[0]]]
syms566 = set(open("/data/p42-t566/symbols.txt").read().replace("\n", ",").split(",")) - {""}
print("S3 files", len(rows), "missing", len(miss), collections.Counter(r[0] for r in miss))
m566 = [r for r in miss if r[1] in syms566]
print("missing among the 566:", len(m566), collections.Counter(r[0] for r in m566))
for r in m566[:20]:
    print(r)
S3 = {(r[0], r[1], r[2]) for r in rows}
extra = [(k, s, d) for k in have for (s, d) in have[k] if (k, s, d) not in S3 and "2026-06-17" <= d <= "2026-11-13"]
print("on disk but not in S3 listing:", len(extra), extra[:5])
for kind in ("aggTrades", "klines"):
    print(kind, "missing GB %.2f" % (sum(int(r[3]) for r in miss if r[0] == kind) / 1e9))
with open(L + "/missing.tsv", "w") as f:
    for r in miss:
        f.write("\t".join(r) + "\n")
