#!/usr/bin/env python3
"""Summarize `alpha collect replay` output lines (rp/summary.txt: '<exchange> <shard> | <line>') into one table: µs per frame (writer + disk thread), parse ns, gunzip ns."""
import collections, re, statistics, sys
rows = collections.OrderedDict()
for l in open(sys.argv[1]):
    m = re.match(r"(\w+) (\w+) \| (.*)", l)
    if not m:
        continue
    ex, sh, rest = m.groups()
    d = rows.setdefault((ex, sh), {"parse": [], "gz": [], "pass": [], "frames": 0, "dups": 0, "hash": set()})
    mm = re.search(r": (\d+) frames", rest)
    if rest.startswith("replay") and mm:
        d["frames"] = int(mm.group(1))
    mm = re.search(r"parse only: ([\d.]+) ns/frame", rest)
    if mm:
        d["parse"].append(float(mm.group(1)))
    mm = re.search(r"gunzip.*?: ([\d.]+) ns/frame", rest)
    if mm:
        d["gz"].append(float(mm.group(1)))
    mm = re.search(r"=> ([\d.]+) CPU-s per Mframe \(wr ([\d.]+), sk ([\d.]+)\); records (\d+) dups (\d+)", rest)
    if mm:
        d["pass"].append((float(mm.group(1)), float(mm.group(2)), float(mm.group(3))))
        d["dups"] = int(mm.group(5))
    mm = re.search(r"fnv ([0-9a-f]{16})", rest)
    if mm:
        d["hash"].add(mm.group(1))
print("| биржа | шард | кадров | дублей, % | разбор, нс/кадр | писатель+диск, мкс/кадр: медиана (мин) | из них писатель / диск | разжатие gzip, нс | хешей разжатого (1 = идентично) |")
print("|---|---|---|---|---|---|---|---|---|")
for (ex, sh), d in rows.items():
    p = sorted(x[0] for x in d["pass"]); wr = sorted(x[1] for x in d["pass"]); sk = sorted(x[2] for x in d["pass"])
    gz = f"{statistics.median(d['gz']):.0f}" if d["gz"] else "—"
    print(f"| {ex} | {sh} | {d['frames']} | {100*d['dups']/max(d['frames'],1):.0f} | {statistics.median(d['parse']):.0f} | {statistics.median(p):.2f} ({p[0]:.2f}) | {statistics.median(wr):.2f} / {statistics.median(sk):.2f} | {gz} | {len(d['hash'])} |")
