#!/usr/bin/env python3
"""TK-393: темп блоков zkSync/Scroll (SQD) — проверка диапазона 15–22.10. Один вызов: имя набора + блоки."""
import json, time, urllib.request, sys
def call(ds, path, q=None):
    for _ in range(12):
        try:
            r = urllib.request.Request(f'https://portal.sqd.dev/datasets/{ds}{path}', data=json.dumps(q).encode() if q else None, headers={'Content-Type': 'application/json', 'User-Agent': 'curl/8'})
            return urllib.request.urlopen(r, timeout=60).read().decode()
        except Exception: time.sleep(10)
def ts(ds, n):
    s = call(ds, '/stream', {"type": "evm", "fromBlock": n, "toBlock": n, "fields": {"block": {"number": True, "timestamp": True}}})
    return json.loads(s.splitlines()[0])['header']['timestamp'] if s and s.strip() else None
ds = sys.argv[1]
h = json.loads(call(ds, '/head'))['number']
print(ds, 'head', h, ts(ds, h), 'h-1e6', ts(ds, h - 1000000), flush=True)
for b in map(int, sys.argv[2:]): print(ds, b, ts(ds, b), flush=True)
