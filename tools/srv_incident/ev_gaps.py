#!/usr/bin/env python3
"""TK-581: все паузы шардов (`gap_end`: gap_start_ms, gap_end_ms, gap_total_ms) по биржам за окно — чтобы проверить, что контрольные окна не попали в чужую паузу и сверить дыры карты символов.
Время — UTC; печатает по бирже: число пауз, сумма шард-секунд, и список пауз >= порога (по умолчанию 3 с): шард, начало–конец, длительность.
python3 ev_gaps.py <HH:MM:SS> <HH:MM:SS> [день] [порог_мс]"""
import json, sys, glob, datetime as dt
a, b = sys.argv[1], sys.argv[2]
day = sys.argv[3] if len(sys.argv) > 3 else "2026-11-16"
thr = int(sys.argv[4]) if len(sys.argv) > 4 else 3000
d0 = dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()
f = lambda s: d0 + int(s[:2]) * 3600 + int(s[3:5]) * 60 + int(s[6:])
t0, t1 = f(a), f(b)
ROOTS = {"binance": "/data/collect/binance/data", "bybit": "/data/turbo-probe/soak-bybit/data", "okx": "/data/collect/okx/data",
         "bingx": "/data/collect/bingx/data", "gate": "/data/collect/gate/data"}
fmt = lambda t: dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%H:%M:%S")
for ex, root in ROOTS.items():
    gs = []
    for p in glob.glob(root + "/events-*.jsonl"):
        for line in open(p):
            if '"gap_end"' not in line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            s = o.get("gap_start_ms", 0) / 1000; e = o.get("gap_end_ms", 0) / 1000
            if e >= t0 and s < t1 and o.get("gap_total_ms", 0) >= thr:
                gs.append((s, e, o["gap_total_ms"], o.get("shard", "?")))
    gs.sort()
    print(f"### {ex}: пауз >= {thr} мс: {len(gs)}, сумма {sum(g[2] for g in gs) / 1000:.0f} шард-с")
    for s, e, tot, sh in gs:
        print(f"   {sh} {fmt(s)}–{fmt(e)} ({tot / 1000:.1f} с)")
