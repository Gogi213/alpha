#!/usr/bin/env python3
"""Траектория лага и скорости перед обрывом: lagtraj.py EX frames.jsonl.zst [шаг_с=2] — по корзинам: кадров/с, лаг p50/max (мс), число записей. Помогает отличить «клиент отстаёт» (лаг растёт монотонно) от «сервер оборвал» (лаг ровный, потом конец)."""
import collections, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stats

ex, path = sys.argv[1], sys.argv[2]
step = float(sys.argv[3]) if len(sys.argv) > 3 else 2.0
hdr, it = stats.lines(path)
t0 = hdr["rx_ns"]
b = collections.defaultdict(list)
end = None
for d in it:
    if "closed" in d or "error" in d:
        end = d
        continue
    if "f" not in d:
        continue
    for (a, sym, e, key, flag, P) in stats.PARSE[ex](d["f"]):
        if e is not None:
            b[int((d["rx_ns"] - t0) / 1e9 / step)].append(d["rx_ns"] // 1_000_000 - e)
for k in sorted(b):
    v = sorted(b[k])
    print(f"t={k * step:6.1f}s recs/s={len(v) / step:8.0f} lag p50={v[len(v) // 2]:5d} max={v[-1]:5d}")
print("end:", end)
