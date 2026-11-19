#!/usr/bin/env python3
"""TK-581: события коллекторов (events-*.jsonl) за окно: счёт по минутам и видам; режим list — все строки (кроме path_ok/subscribed/stats).
python3 ev_timeline.py <HH:MM:SS> <HH:MM:SS> [count|list] [день=2026-11-16] [биржи через запятую]"""
import json, sys, glob, collections, datetime as dt
a, b = sys.argv[1], sys.argv[2]
mode = sys.argv[3] if len(sys.argv) > 3 else "count"
day = sys.argv[4] if len(sys.argv) > 4 else "2026-11-16"
only = sys.argv[5].split(",") if len(sys.argv) > 5 else None
d0 = dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()
f = lambda s: d0 + int(s[:2]) * 3600 + int(s[3:5]) * 60 + int(s[6:])
t0, t1 = f(a), f(b)
ROOTS = {"binance": "/data/collect/binance/data", "bybit": "/data/turbo-probe/soak-bybit/data", "okx": "/data/collect/okx/data",
         "bingx": "/data/collect/bingx/data", "gate": "/data/collect/gate/data"}
SKIP = {"path_ok", "subscribed", "stats"}
for ex, root in ROOTS.items():
    if only and ex not in only:
        continue
    evs = []
    for p in sorted(glob.glob(root + "/events-*.jsonl")):
        for line in open(p):
            if '"ts_ms"' not in line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            t = o.get("ts_ms", 0) / 1000
            if t0 <= t < t1:
                evs.append((t, o))
    evs.sort(key=lambda x: x[0])
    print(f"### {ex}: {len(evs)} событий")
    if mode == "count":
        c = collections.defaultdict(collections.Counter)
        for t, o in evs:
            c[int((t - t0) // 60)][o.get("ev")] += 1
        for k in sorted(c):
            ts = dt.datetime.fromtimestamp(t0 + k * 60, dt.timezone.utc).strftime("%H:%M")
            print(ts, dict(sorted(c[k].items())))
    else:
        for t, o in evs:
            if o.get("ev") in SKIP:
                continue
            keep = {k: v for k, v in o.items() if k not in ("diag", "tcp", "hs", "ts_ms", "url", "entrance")}
            ts = dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%H:%M:%S.%f")[:-3]
            print(ts, json.dumps(keep, ensure_ascii=False, separators=(",", ":"))[:330])
