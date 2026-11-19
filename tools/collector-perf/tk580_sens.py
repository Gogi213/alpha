#!/usr/bin/env python3
"""TK-580 A: prod queue_lag_p99 vs host load (sar 10 s idle/steal) and market rate, per exchange/kind.
usage: sens.py [from_epoch]   (reads /data/*/stats-*.jsonl, /var/log/sysstat/sa13..sa16)"""
import glob, json, subprocess, sys, statistics, datetime, collections, os
T0 = int(sys.argv[1]) if len(sys.argv) > 1 else int(datetime.datetime(2026, 11, 14, tzinfo=datetime.timezone.utc).timestamp())
ROOTS = {"bingx": "/data/collect/bingx/data", "okx": "/data/collect/okx/data", "gate": "/data/collect/gate/data",
         "bybit": "/data/turbo-probe/soak-bybit/data", "binance": "/data/collect/binance/data"}
host = {}  # 10 s bin -> (idle, steal)
for day in (13, 14, 15, 16, 17):
    f = f"/var/log/sysstat/sa{day:02d}"
    if not os.path.exists(f):
        continue
    d0 = int(datetime.datetime(2026, 11, day, tzinfo=datetime.timezone.utc).timestamp())
    out = subprocess.run(["sar", "-u", "-f", f], capture_output=True, text=True, env={**os.environ, "LC_ALL": "C", "S_TIME_FORMAT": "ISO"}).stdout
    for l in out.splitlines():
        p = l.split()
        if len(p) == 8 and p[1] == "all":
            try:
                hh, mm, ss = map(int, p[0].split(":"))
            except ValueError:
                continue
            host[(d0 + hh * 3600 + mm * 60 + ss) // 10] = (float(p[7]), float(p[5]))
def nearest(t):
    b = t // 10
    for k in (b, b - 1, b + 1):
        if k in host:
            return host[k]
    return None
EDGES = [(0, 20), (20, 35), (35, 50), (50, 65), (65, 101)]
def cell(v):
    return f"{statistics.median(v):5.1f}/{statistics.quantiles(v, n=10)[8] if len(v) >= 10 else float('nan'):5.1f} n{len(v):5d}" if v else "   -"
print("queue_lag_p99_ms, median/p90 per 10 s row, binned by host idle %; rows with t%3600<=120 or t%300<=20 dropped (hour/5-min spikes)")
print("exchange kind | " + " | ".join(f"idle {a}-{b}" for a, b in EDGES))
res = {}
for ex, root in ROOTS.items():
    for f in sorted(glob.glob(root + "/stats-*.jsonl")):
        sh = f.split("stats-")[1][:-6]
        kind = sh.rstrip("0123456789")
        for l in open(f, errors="replace"):
            try:
                d = json.loads(l)
            except ValueError:
                continue
            t = (d.get("ts_ms") or 0) // 1000
            q = d.get("queue_lag_p99_ms")
            if q is None or t < T0 or t % 3600 <= 120 or t % 300 <= 20:
                continue
            h = nearest(t)
            if h is None:
                continue
            res.setdefault((ex, kind), []).append((t, q, h[0], h[1], d.get("msgs", 0)))
json.dump({f"{k[0]}/{k[1]}": v for k, v in res.items()}, open("/data/tk580/sens_rows.json", "w"))
for (ex, kind), rows in sorted(res.items()):
    cells = []
    for a, b in EDGES:
        cells.append(cell([q for t, q, i, s, m in rows if a <= i < b]))
    print(f"{ex} {kind} | " + " | ".join(cells))
