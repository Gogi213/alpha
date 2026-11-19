#!/usr/bin/env python3
"""Смещение часов биржи относительно srv по REST-времени: server_ms − середина запроса, 30 замеров по одному keep-alive соединению (первый — прогрев).
  clock.py HOST PATH KEYPATH      например: clock.py api.bitget.com /api/v2/public/time data.serverTime
Печатает p50 смещения и p50 RTT (оценка ±RTT/2: отделить смещение часов от доставки нельзя)."""
import http.client, json, statistics, sys, time

host, path, key = sys.argv[1:4]
c = http.client.HTTPSConnection(host, timeout=15)
off, rtt = [], []
for i in range(31):
    t0 = time.time()
    c.request("GET", path, headers={"User-Agent": "alpha-collector/clock"})
    d = json.loads(c.getresponse().read())
    t1 = time.time()
    for k in key.split("."):
        d = d[k]
    if i:
        off.append(int(d) - (t0 + t1) / 2 * 1000)
        rtt.append((t1 - t0) * 1000)
print(json.dumps({"host": host, "offset_ms_p50": round(statistics.median(off), 1), "offset_ms_min": round(min(off), 1), "offset_ms_max": round(max(off), 1), "rtt_ms_p50": round(statistics.median(rtt), 1), "n": len(off)}))
