#!/usr/bin/env python3
"""TK-581: netwatch по секундам: строка = секунда UTC, ячейка = RTT мс цели (T = таймаут, . = нет записи).
python3 nw_sec.py <HH:MM:SS> <HH:MM:SS> [день]"""
import json, sys, datetime as dt
a, b = sys.argv[1], sys.argv[2]
day = sys.argv[3] if len(sys.argv) > 3 else "2026-11-16"
d0 = dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()
f = lambda s: d0 + int(s[:2]) * 3600 + int(s[3:5]) * 60 + int(s[6:])
t0, t1 = f(a), f(b)
rows = {}
for line in open(f"/data/netwatch/{day}.jsonl"):
    try:
        o = json.loads(line)
    except Exception:
        continue
    if "ts" in o and t0 <= o["ts"] < t1:
        rows.setdefault(int(o["ts"] - t0), {})[o["n"]] = o["rtt"]
names = ["binance", "bybit", "okx", "bingx", "gateway", "neutral"]
print("t_utc   " + " ".join(f"{n[:7]:>7}" for n in names))
for k in range(int(t1 - t0)):
    ts = dt.datetime.fromtimestamp(t0 + k, dt.timezone.utc).strftime("%H:%M:%S")
    r = rows.get(k, {})
    cs = []
    for n in names:
        if n not in r: cs.append("      .")
        elif r[n] is None: cs.append("      T")
        else: cs.append(f"{r[n]:7.0f}")
    print(ts + " " + " ".join(cs))
