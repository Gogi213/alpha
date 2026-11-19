#!/usr/bin/env python3
"""TK-581: series-minutes.jsonl (минутный ряд коллектора: gaps, lost_ids, lost_ms, pause_ms, late[book,trade,mark,raw], to) за окно.
Печатает минуты, где что-то не ноль, и итог за окно по бирже. python3 series_min.py <HH:MM> <HH:MM> [день]"""
import json, sys, datetime as dt
a, b = sys.argv[1], sys.argv[2]
day = sys.argv[3] if len(sys.argv) > 3 else "2026-11-16"
d0 = dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()
f = lambda s: d0 + int(s[:2]) * 3600 + int(s[3:5]) * 60
t0, t1 = f(a), f(b)
ROOTS = {"binance": "/data/collect/binance/data", "bybit": "/data/turbo-probe/soak-bybit/data", "okx": "/data/collect/okx/data",
         "bingx": "/data/collect/bingx/data", "gate": "/data/collect/gate/data"}
for ex, root in ROOTS.items():
    tot = {"gaps": 0, "lost_ids": 0, "lost_ms": 0, "pause_ms": 0, "late": [0, 0, 0, 0], "to": [0, 0]}
    n = 0
    print(f"### {ex}")
    for line in open(root + "/series-minutes.jsonl"):
        try:
            o = json.loads(line)
        except Exception:
            continue
        if not (t0 <= o.get("t", 0) < t1):
            continue
        n += 1
        for k in ("gaps", "lost_ids", "lost_ms", "pause_ms"):
            tot[k] += o.get(k) or 0
        for k in ("late", "to"):
            for i, v in enumerate(o.get(k) or []):
                tot[k][i] += v or 0
        if o.get("gaps") or o.get("lost_ids") or o.get("lost_ms") or o.get("pause_ms") or sum(o.get("late") or []) or sum(o.get("to") or []):
            print(dt.datetime.fromtimestamp(o["t"], dt.timezone.utc).strftime("%H:%M"), {k: v for k, v in o.items() if k != "t" and v not in (0, [0, 0, 0, 0], [0, 0])})
    print(f"  минут {n}; итог {tot}")
