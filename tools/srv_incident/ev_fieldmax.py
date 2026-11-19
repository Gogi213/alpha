#!/usr/bin/env python3
"""TK-581: максимум по каждому числовому полю каждого типа события `events-*.jsonl` за окно (где искать «RTT до 9,5 с»). ts_ms и session пропускаются.
python3 ev_fieldmax.py <HH:MM:SS> <HH:MM:SS> [день]"""
import json, sys, glob, collections, datetime as dt
a, b = sys.argv[1], sys.argv[2]
day = sys.argv[3] if len(sys.argv) > 3 else "2026-11-16"
d0 = dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()
f = lambda s: d0 + int(s[:2]) * 3600 + int(s[3:5]) * 60 + int(s[6:])
t0, t1 = f(a), f(b)
ROOTS = {"binance": "/data/collect/binance/data", "bybit": "/data/turbo-probe/soak-bybit/data", "okx": "/data/collect/okx/data",
         "bingx": "/data/collect/bingx/data", "gate": "/data/collect/gate/data"}
fmt = lambda t: dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%H:%M:%S")
for ex, root in ROOTS.items():
    mx = {}
    for p in glob.glob(root + "/events-*.jsonl"):
        for line in open(p):
            try:
                o = json.loads(line)
            except Exception:
                continue
            t = o.get("ts_ms", 0) / 1000
            if not (t0 <= t < t1):
                continue
            for k, v in o.items():
                if k in ("ts_ms", "session", "n") or isinstance(v, bool) or not isinstance(v, (int, float)):
                    continue
                key = (o.get("ev"), k)
                if key not in mx or v > mx[key][0]:
                    mx[key] = (v, t, o.get("shard", "?"))
    big = sorted(((v, ev, k, t, sh) for (ev, k), (v, t, sh) in mx.items() if ("ms" in k or "rtt" in k) and v >= 2000), reverse=True)
    print(f"### {ex} [{a}–{b}] поля в мс >= 2000:", "; ".join(f"{ev}.{k}={v:.0f}@{fmt(t)}/{sh}" for v, ev, k, t, sh in big[:12]))
