#!/usr/bin/env python3
"""TK-581: пересчёт двух чисел из TK-580 review: «RTT подключения до 9,5 с» (BingX; 8,9 OKX, 7,8 Bybit, 7,2 Gate, 8,2 Binance) и «book path_ok p50 348 мс при пороге 350».
По сырым событиям `events-*.jsonl` (поля `connected.rtt_ms`, `path_rejected_rtt.rtt_ms`, `path_ok.p50_ms`) за окно: максимум и первые 5 значений с временем и шардом.
python3 ev_check580.py <HH:MM:SS> <HH:MM:SS> [день]"""
import json, sys, glob, datetime as dt
a, b = sys.argv[1], sys.argv[2]
day = sys.argv[3] if len(sys.argv) > 3 else "2026-11-16"
d0 = dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()
f = lambda s: d0 + int(s[:2]) * 3600 + int(s[3:5]) * 60 + int(s[6:])
t0, t1 = f(a), f(b)
ROOTS = {"binance": "/data/collect/binance/data", "bybit": "/data/turbo-probe/soak-bybit/data", "okx": "/data/collect/okx/data",
         "bingx": "/data/collect/bingx/data", "gate": "/data/collect/gate/data"}
fmt = lambda t: dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%H:%M:%S")
for ex, root in ROOTS.items():
    conn, rej, ok = [], [], []
    for p in glob.glob(root + "/events-*.jsonl"):
        for line in open(p):
            if '"connected"' not in line and '"path_rejected_rtt"' not in line and '"path_ok"' not in line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            t = o.get("ts_ms", 0) / 1000
            if not (t0 <= t < t1):
                continue
            e = o.get("ev"); sh = o.get("shard", "?")
            if e == "connected" and o.get("rtt_ms") is not None:
                conn.append((o["rtt_ms"], t, sh))
            elif e == "path_rejected_rtt" and o.get("rtt_ms") is not None:
                rej.append((o["rtt_ms"], t, sh, o.get("limit_ms")))
            elif e == "path_ok" and o.get("p50_ms") is not None:
                ok.append((o["p50_ms"], t, sh, o.get("n"), o.get("p99_ms")))
    top = lambda xs: ", ".join(f"{x[0]:.0f}@{fmt(x[1])}/{x[2]}" for x in sorted(xs, reverse=True)[:5])
    print(f"### {ex} [{a}–{b}]: connected {len(conn)} (макс {max(conn)[0] if conn else '-'}): {top(conn)}")
    print(f"    path_rejected_rtt {len(rej)} (макс {max(rej)[0] if rej else '-'}): {top(rej)}")
    print(f"    path_ok {len(ok)}: p50 макс {max(ok)[0] if ok else '-'}: {top(ok)}")
    if ex == "bingx":
        for x in sorted(ok, key=lambda x: x[1]):
            if f("21:05:00") <= x[1] < f("21:08:00"):
                print("      path_ok 21:05–21:08:", fmt(x[1]), x[2], "p50", x[0], "p99", x[4], "n", x[3])
