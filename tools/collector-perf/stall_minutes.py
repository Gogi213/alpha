#!/usr/bin/env python3
"""G-022 collector-perf: baseline «stall minutes» from events-*.jsonl `gap_end` (data pause > 3 s on a shard with a live connection).
usage: stall_minutes.py <hours> <exchange=root> ... ; prints per exchange and srv-wide (union) the number of UTC minutes with >= 1 gap, total gap seconds, and the biggest."""
import glob, json, sys, time, collections
H = float(sys.argv[1]); now = time.time() * 1000; t0 = now - H * 3600e3
per = {}
for spec in sys.argv[2:]:
    ex, root = spec.split("=")
    mins = collections.Counter(); tot = 0; big = (0, None); n = 0; shards = set()
    for f in glob.glob(f"{root}/events-*.jsonl"):
        for line in open(f, errors="replace"):
            if '"gap_end"' not in line:
                continue
            try:
                j = json.loads(line)
            except Exception:
                continue
            st = j.get("gap_start_ms") or j.get("data", {}).get("gap_start_ms")
            d = j.get("gap_total_ms") or j.get("data", {}).get("gap_total_ms")
            if st is None or d is None:
                continue
            if st < t0:
                continue
            n += 1; tot += d; shards.add(f.split("events-")[1][:-6])
            for m in range(int(st // 60000), int((st + d) // 60000) + 1):
                mins[m] += 1
            if d > big[0]:
                big = (d, st)
    per[ex] = mins
    print(f"{ex:8s} gaps {n:4d}  minutes with a gap {len(mins):4d}  total gap {tot/1000:8.0f} s  biggest {big[0]/1000:.0f} s  shards {len(shards)}")
allm = set()
for m in per.values():
    allm |= set(m)
print(f"srv-wide (union of exchanges): minutes with >= 1 gap {len(allm)} of {int(H*60)} ({100*len(allm)/(H*60):.1f} %)")
for k in (2, 3, 4):
    c = collections.Counter()
    for m in per.values():
        for x in m: c[x] += 1
    print(f"   minutes where >= {k} exchanges had a gap: {sum(1 for v in c.values() if v >= k)}")
