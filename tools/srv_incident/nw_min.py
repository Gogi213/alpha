#!/usr/bin/env python3
"""TK-581: netwatch за окно — по минутам и целям: замеров, таймаутов, медиана / p95 / макс RTT (мс).
Запуск на srv: python3 nw_min.py <HH:MM начало> <HH:MM конец> [шаг_с=60] [день=2026-11-16]"""
import json, sys, datetime as dt
a, b = sys.argv[1], sys.argv[2]
step = int(sys.argv[3]) if len(sys.argv) > 3 else 60
day = sys.argv[4] if len(sys.argv) > 4 else "2026-11-16"
d0 = dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()
t0 = d0 + int(a[:2]) * 3600 + int(a[3:]) * 60
t1 = d0 + int(b[:2]) * 3600 + int(b[3:]) * 60
rows = {}
for line in open(f"/data/netwatch/{day}.jsonl"):
    try:
        o = json.loads(line)
    except Exception:
        continue
    if "ts" not in o or not (t0 <= o["ts"] < t1):
        continue
    k = int((o["ts"] - t0) // step)
    rows.setdefault(k, {}).setdefault(o["n"], []).append(o["rtt"])
names = ["binance", "bybit", "okx", "bingx", "gateway", "neutral"]
print("t_utc  | " + " | ".join(f"{n:^22}" for n in names) + "   (замеров/таймаутов med p95 max, мс)")
for k in sorted(rows):
    ts = dt.datetime.fromtimestamp(t0 + k * step, dt.timezone.utc).strftime("%H:%M:%S")
    cells = []
    for n in names:
        v = rows[k].get(n, [])
        ok = sorted(x for x in v if x is not None)
        to = sum(1 for x in v if x is None)
        if ok:
            cells.append(f"{len(v):2d}/{to:2d} {ok[len(ok)//2]:6.0f} {ok[min(len(ok)-1,int(len(ok)*.95))]:6.0f} {ok[-1]:6.0f}")
        else:
            cells.append(f"{len(v):2d}/{to:2d}      -      -      -")
    print(f"{ts} | " + " | ".join(cells))
