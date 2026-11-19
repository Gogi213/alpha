#!/usr/bin/env python3
"""TK-581: хвосты queue_lag окна W1 TK-580 (копии BingX old/new рядом с боевым): по 10-с слотам максимум queue_lag_p99 по шардам копий и боевого BingX, плюс
msgs боевого. Показывает, совпадают ли хвосты копий по времени с общим происшествием (боевой тоже) или стоят отдельно.
python3 w1_tails.py <окно: ab-w1|ab-w2|...> <HH:MM:SS> <HH:MM:SS>"""
import json, sys, glob, collections, datetime as dt
win, a, b = sys.argv[1:4]
d0 = dt.datetime.strptime("2026-11-16", "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()
f = lambda s: d0 + int(s[:2]) * 3600 + int(s[3:5]) * 60 + int(s[6:])
t0, t1 = f(a), f(b)
def load(pattern):
    slots = collections.defaultdict(list)
    for p in glob.glob(pattern):
        for line in open(p):
            if '"ts_ms"' not in line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            t = o["ts_ms"] / 1000
            if t0 <= t < t1:
                slots[int(t // 10) * 10].append(o)
    return slots
src = {"prod": "/data/collect/bingx/data/stats-*.jsonl"}
for d in sorted(glob.glob(f"/data/tk580/{win}/*/data")):
    src[d.split("/")[-2]] = d + "/stats-*.jsonl"
S = {k: load(v) for k, v in src.items()}
q = lambda L: max((o.get("queue_lag_p99_ms") or 0) for o in L) if L else None
print("слот   " + " ".join(f"{k:>10}" for k in S) + "   prod_msgs")
for s in sorted(set().union(*[set(v) for v in S.values()])):
    row = []
    for k in S:
        v = q(S[k].get(s, []))
        row.append("         -" if v is None else f"{v:10.1f}")
    pm = sum((o.get("msgs") or 0) for o in S["prod"].get(s, []))
    print(dt.datetime.fromtimestamp(s, dt.timezone.utc).strftime("%H:%M:%S"), " ".join(row), f"  {pm}")
