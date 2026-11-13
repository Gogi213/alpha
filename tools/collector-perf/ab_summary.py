#!/usr/bin/env python3
"""Interleaved A/B of replay binaries: median and min of (writer+disk) µs/frame and parse ns per variant per shard, hash agreement."""
import collections, re, statistics, sys
d = collections.defaultdict(lambda: {"pass": [], "parse": [], "hash": set()})
for l in open(sys.argv[1]):
    m = re.match(r"(\w+) (\w+) (\w+) \| (.*)", l)
    if not m:
        continue
    ex, sh, v, rest = m.groups()
    k = (ex, sh, v)
    mm = re.search(r"=> ([\d.]+) CPU-s per Mframe", rest)
    if mm: d[k]["pass"].append(float(mm.group(1)))
    mm = re.search(r"parse only: ([\d.]+) ns", rest)
    if mm: d[k]["parse"].append(float(mm.group(1)))
    mm = re.search(r"fnv ([0-9a-f]{16})", rest)
    if mm: d[k]["hash"].add(mm.group(1))
print("| шард | вариант | писатель+диск, мкс/кадр: медиана (мин) | разбор, нс/кадр: медиана | хеш разжатого |")
print("|---|---|---|---|---|")
base = {}
for (ex, sh, v), x in sorted(d.items(), key=lambda kv: (kv[0][0], kv[0][1], {"base2": 0, "lto": 1, "ltomi": 2}.get(kv[0][2], 9))):
    p = sorted(x["pass"]); med = statistics.median(p)
    if v == "base2": base[(ex, sh)] = (med, statistics.median(x["parse"]))
    b = base.get((ex, sh), (med, 1))
    print(f"| {ex} {sh} | {v} | {med:.2f} ({p[0]:.2f}) [{100*(med/b[0]-1):+.0f} %] | {statistics.median(x['parse']):.0f} [{100*(statistics.median(x['parse'])/b[1]-1):+.0f} %] | {','.join(sorted(x['hash']))} |")
