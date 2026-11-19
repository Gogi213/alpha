#!/usr/bin/env python3
"""TK-581: общие эпизоды по netwatch: минута «плохая» для цели, если таймаутов >= 2 или замеров < 50 из 60 (connect висел), либо RTT-макс >= 1100 мс
(для Binance — >= 1500: фон Binance 240 мс); эпизод = плохо у >= 3 из 4 TCP-целей. Шлюз/нейтральная цель — только справа (потери, макс).
python3 nw_episodes.py <день> [<день> ...]"""
import json, sys, collections, datetime as dt
TCP = ["binance", "bybit", "okx", "bingx"]
for day in sys.argv[1:]:
    d0 = dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()
    cell = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0, 0.0]))  # min -> n -> [замеров, таймаутов, макс]
    for line in open(f"/data/netwatch/{day}.jsonl"):
        try:
            o = json.loads(line)
        except Exception:
            continue
        if "ts" not in o:
            continue
        m = int((o["ts"] - d0) // 60)
        c = cell[m][o["n"]]
        c[0] += 1
        if o["rtt"] is None:
            c[1] += 1
        else:
            c[2] = max(c[2], o["rtt"])
    eps = []
    for m in sorted(cell):
        bad = 0
        for n in TCP:
            c = cell[m].get(n, [0, 0, 0.0])
            lim = 1500 if n == "binance" else 1100
            if c[1] >= 2 or c[0] < 50 or c[2] >= lim:
                bad += 1
        if bad >= 3:
            g = cell[m].get("gateway", [0, 0, 0.0]); nn = cell[m].get("neutral", [0, 0, 0.0])
            eps.append((m, bad, g, nn))
    print(f"=== {day}: эпизодов-минут {len(eps)}")
    for m, bad, g, nn in eps:
        print(dt.datetime.fromtimestamp(d0 + m * 60, dt.timezone.utc).strftime("%H:%M"), f"плохих целей {bad}/4", f"шлюз {g[0]}/{g[1]} макс {g[2]:.0f}", f"8.8.8.8 {nn[0]}/{nn[1]} макс {nn[2]:.0f}")
