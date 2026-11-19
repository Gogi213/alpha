#!/usr/bin/env python3
"""TK-581: RTT подключений (`connected.rtt_ms`) и отбраковки пути (`path_rejected*`) по биржам за окно: максимум, число, p50 по минутам.
python3 ev_rtt.py <HH:MM:SS> <HH:MM:SS> [день]"""
import json, sys, glob, collections, datetime as dt
a, b = sys.argv[1], sys.argv[2]
day = sys.argv[3] if len(sys.argv) > 3 else "2026-11-16"
d0 = dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()
f = lambda s: d0 + int(s[:2]) * 3600 + int(s[3:5]) * 60 + int(s[6:])
t0, t1 = f(a), f(b)
ROOTS = {"binance": "/data/collect/binance/data", "bybit": "/data/turbo-probe/soak-bybit/data", "okx": "/data/collect/okx/data",
         "bingx": "/data/collect/bingx/data", "gate": "/data/collect/gate/data"}
for ex, root in ROOTS.items():
    conn = []; rej = []; rej_rtt = []
    for p in glob.glob(root + "/events-*.jsonl"):
        for line in open(p):
            if '"ts_ms"' not in line or not ('"connected"' in line or "path_rejected" in line):
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            t = o.get("ts_ms", 0) / 1000
            if not (t0 <= t < t1):
                continue
            e = o.get("ev")
            if e == "connected" and o.get("rtt_ms") is not None:
                conn.append((t, o["rtt_ms"], o.get("shard")))
            elif e == "path_rejected":
                rej.append((t, o))
            elif e == "path_rejected_rtt":
                rej_rtt.append((t, o))
    conn.sort(key=lambda x: -x[1])
    print(f"### {ex}: connected {len(conn)}; top RTT: " + ", ".join(f"{r:.0f}мс@{dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime('%H:%M:%S')}" for t, r, s in conn[:6]))
    print(f"    path_rejected (по лагу) {len(rej)}, path_rejected_rtt {len(rej_rtt)}")
    for t, o in sorted(rej)[:14]:
        print("     ", dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%H:%M:%S"), {k: v for k, v in o.items() if k not in ("ts_ms", "ev", "diag", "tcp")})
    if rej_rtt:
        vals = sorted(o.get("rtt_ms") or o.get("p50_ms") or 0 for t, o in rej_rtt)
        print("    rtt_rejected значения: min", vals[0], "p50", vals[len(vals)//2], "max", vals[-1], "; ключи:", list(rej_rtt[0][1].keys())[:12])
