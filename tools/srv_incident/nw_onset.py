#!/usr/bin/env python3
"""TK-581: старт и конец эпизода по netwatch: секунды, где >= 2 из 4 TCP-целей «плохи» (таймаут / нет замера из-за зависшего connect / RTT >= 1000, Binance >= 1500),
сгруппированные в эпизоды (разрыв между плохими секундами <= 20 с). Для каждого: первая и последняя плохая секунда, число плохих секунд, ICMP шлюза/8.8.8.8 за это время
(замеров / таймаутов / макс. RTT). python3 nw_onset.py <HH:MM:SS> <HH:MM:SS> [день]"""
import json, sys, collections, datetime as dt
a, b = sys.argv[1], sys.argv[2]
day = sys.argv[3] if len(sys.argv) > 3 else "2026-11-16"
d0 = dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()
f = lambda s: int(d0 + int(s[:2]) * 3600 + int(s[3:5]) * 60 + int(s[6:]))
t0, t1 = f(a), f(b)
TCP = ["binance", "bybit", "okx", "bingx"]
sec = collections.defaultdict(dict)
for line in open(f"/data/netwatch/{day}.jsonl"):
    try:
        o = json.loads(line)
    except Exception:
        continue
    if "ts" in o and t0 <= o["ts"] < t1:
        sec[int(o["ts"])][o["n"]] = o["rtt"]
bad_secs = []
for s in range(t0, t1):
    nbad = 0
    for n in TCP:
        v = sec[s].get(n, "missing")
        if v == "missing" or v is None or v >= (1500 if n == "binance" else 1000):
            nbad += 1
    if nbad >= 2:
        bad_secs.append(s)
eps = []
for s in bad_secs:
    if eps and s - eps[-1][-1] <= 20:
        eps[-1].append(s)
    else:
        eps.append([s])
fmt = lambda s: dt.datetime.fromtimestamp(s, dt.timezone.utc).strftime("%H:%M:%S")
for e in eps:
    if len(e) < 3:
        continue
    lo, hi = e[0], e[-1]
    icmp = {n: [0, 0, 0.0] for n in ("gateway", "neutral")}
    for s in range(lo, hi + 1):
        for n in icmp:
            if n in sec[s]:
                icmp[n][0] += 1
                if sec[s][n] is None:
                    icmp[n][1] += 1
                else:
                    icmp[n][2] = max(icmp[n][2], sec[s][n])
    print(f"{fmt(lo)} – {fmt(hi)} ({hi-lo+1} с, плохих секунд {len(e)}); ICMP шлюз {icmp['gateway'][0]}/{icmp['gateway'][1]} макс {icmp['gateway'][2]:.0f}; 8.8.8.8 {icmp['neutral'][0]}/{icmp['neutral'][1]} макс {icmp['neutral'][2]:.0f}")
