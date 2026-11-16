#!/usr/bin/env python3
"""TK-581: плохие секунды netwatch по каждой TCP-цели по минутам (плохо = таймаут / нет замера / RTT >= 1000, Binance >= 1500): видно, когда плохо у одной цели, а когда у нескольких.
Печатает минуты, где у какой-либо цели >= 3 плохих секунд. python3 nw_target_minutes.py <HH:MM> <HH:MM> [день]"""
import json, sys, collections, datetime as dt
a, b = sys.argv[1], sys.argv[2]
day = sys.argv[3] if len(sys.argv) > 3 else "2026-11-16"
d0 = int(dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp())
t0, t1 = d0 + int(a[:2]) * 3600 + int(a[3:5]) * 60, d0 + int(b[:2]) * 3600 + int(b[3:5]) * 60
TCP = ["binance", "bybit", "okx", "bingx"]
sec = collections.defaultdict(dict)
for line in open(f"/data/netwatch/{day}.jsonl"):
    try:
        o = json.loads(line)
    except Exception:
        continue
    if "ts" in o and t0 <= o["ts"] < t1:
        sec[int(o["ts"])][o["n"]] = o["rtt"]
print("минута  плохих секунд: binance bybit okx bingx")
for m in range(t0, t1, 60):
    c = {n: 0 for n in TCP}
    for s in range(m, m + 60):
        for n in TCP:
            v = sec[s].get(n, "missing")
            if v == "missing" or v is None or v >= (1500 if n == "binance" else 1000):
                c[n] += 1
    if max(c.values()) >= 3:
        print(f"{(m - d0) // 3600:02d}:{(m - d0) % 3600 // 60:02d}   " + " ".join(f"{c[n]:>5}" for n in TCP))
