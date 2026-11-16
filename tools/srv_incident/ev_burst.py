#!/usr/bin/env python3
"""TK-581: «общие эпизоды»: минуты, где события timeout / path_rejected_rtt / ping_degraded идут сразу у >= 3 бирж. Показывает, как часто и в какие минуты часа.
python3 ev_burst.py <день> [мин_событий_на_биржу=2] [мин_бирж=3]"""
import json, sys, glob, collections, datetime as dt
day = sys.argv[1]
mn = int(sys.argv[2]) if len(sys.argv) > 2 else 2
mx = int(sys.argv[3]) if len(sys.argv) > 3 else 3
d0 = dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()
ROOTS = {"binance": "/data/collect/binance/data", "bybit": "/data/turbo-probe/soak-bybit/data", "okx": "/data/collect/okx/data",
         "bingx": "/data/collect/bingx/data", "gate": "/data/collect/gate/data"}
KINDS = ("timeout", "path_rejected_rtt", "ping_degraded")
per = collections.defaultdict(lambda: collections.defaultdict(int))  # minute -> ex -> n
for ex, root in ROOTS.items():
    for p in glob.glob(root + "/events-*.jsonl"):
        for line in open(p):
            if '"ts_ms"' not in line or not any(k in line for k in KINDS):
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            if o.get("ev") not in KINDS:
                continue
            t = o["ts_ms"] / 1000
            if d0 <= t < d0 + 86400:
                per[int((t - d0) // 60)][ex] += 1
tot = 0
for m in sorted(per):
    hit = {e: n for e, n in per[m].items() if n >= mn}
    if len(hit) >= mx:
        tot += 1
        print(dt.datetime.fromtimestamp(d0 + m * 60, dt.timezone.utc).strftime("%H:%M"), dict(sorted(per[m].items())))
print("минут-эпизодов:", tot)
