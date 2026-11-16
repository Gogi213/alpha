#!/usr/bin/env python3
"""TK-581: stats-<шард>.jsonl (10-с строки коллектора) за окно: по бирже и 10-с слоту — максимум по шардам queue_lag_p99, busy_max_us, handoff_max_us,
disk_write_max_us, ring_blocked_ms, сумма msgs; медиана lag_p50. Печатает слоты, где queue_lag_p99 >= порога (по умолчанию 1000 мс), ring_blocked > 0 или msgs < 50 % медианы окна.
python3 stats10.py <HH:MM:SS> <HH:MM:SS> [порог_мс=30] [день=2026-11-16]"""
import json, sys, glob, collections, statistics as st, datetime as dt
a, b = sys.argv[1], sys.argv[2]
thr = float(sys.argv[3]) if len(sys.argv) > 3 else 1000.0
day = sys.argv[4] if len(sys.argv) > 4 else "2026-11-16"
d0 = dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()
f = lambda s: d0 + int(s[:2]) * 3600 + int(s[3:5]) * 60 + int(s[6:])
t0, t1 = f(a), f(b)
ROOTS = {"binance": "/data/collect/binance/data", "bybit": "/data/turbo-probe/soak-bybit/data", "okx": "/data/collect/okx/data",
         "bingx": "/data/collect/bingx/data", "gate": "/data/collect/gate/data"}
for ex, root in ROOTS.items():
    slots = collections.defaultdict(list)
    for p in glob.glob(root + "/stats-*.jsonl"):
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
    print(f"### {ex}: слотов {len(slots)}")
    allq, allm = [], []
    base = st.median(sum((o.get("msgs") or 0) for o in L) for L in slots.values()) if slots else 0
    for s in sorted(slots):
        L = slots[s]
        g = lambda o, k: o.get(k) or 0
        q = max(g(o, "queue_lag_p99_ms") for o in L); allq.append(q)
        rb = max(g(o, "ring_blocked_ms") for o in L)
        msgs = sum(g(o, "msgs") for o in L); allm.append(msgs)
        if q >= thr or rb > 0 or msgs < 0.5 * base:
            print(dt.datetime.fromtimestamp(s, dt.timezone.utc).strftime("%H:%M:%S"), f"shards={len(L):2d} qlag_p99max={q:8.1f} msgs_ratio={msgs/base:4.2f}",
                  f"busy_max={max((o.get('busy_max_us') or 0) for o in L)/1000:7.1f}мс handoff={max((o.get('handoff_max_us') or 0) for o in L)/1000:7.1f}мс",
                  f"disk_w={max((o.get('disk_write_max_us') or 0) for o in L)/1000:6.1f}мс ring_blocked={rb} msgs={msgs}",
                  f"lag_p50 med={st.median((o.get('lag_p50_ms') or 0) for o in L):.0f}")
    if allq:
        allq.sort()
        print(f"  всего слотов {len(allq)}; queue_lag_p99 max по слотам: медиана {allq[len(allq)//2]:.1f}, p95 {allq[int(len(allq)*.95)]:.1f}, макс {allq[-1]:.1f}")
