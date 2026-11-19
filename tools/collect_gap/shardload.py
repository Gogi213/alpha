#!/usr/bin/env python3
"""Нагрузка и лаг по шардам за окно: кадров/с (`msgs` окна 10 с / 10, обе линии шарда), лаг p50 (взвешен кадрами) и p99 (среднее / максимум окон) из stats-<шард>.jsonl.
Запуск: shardload.py <корень> <t0_s> <t1_s> [префикс шарда: trade|book|mark]; печатает строку на шард и макс/мин нагрузки по виду."""
import glob, json, os, sys

root, t0, t1 = sys.argv[1], int(sys.argv[2]) * 1000, int(sys.argv[3]) * 1000
only = sys.argv[4] if len(sys.argv) > 4 else ''
rows = {}
for f in sorted(glob.glob(os.path.join(root, 'stats-*.jsonl'))):
    shard = os.path.basename(f)[6:-6]
    if only and not shard.startswith(only):
        continue
    n = msgs = 0
    lw = 0.0
    p99s = []
    with open(f, 'rb') as fh:
        fh.seek(max(0, os.path.getsize(f) - 40_000_000))
        for ln in fh:
            try:
                v = json.loads(ln)
            except ValueError:
                continue
            ts, w, p50 = v.get('ts_ms'), v.get('msgs') or 0, v.get('lag_p50_ms')
            if ts is None or ts < t0 or ts > t1 or w <= 0 or p50 is None:
                continue
            n += 1
            msgs += w
            lw += p50 * w
            if v.get('lag_p99_ms') is not None:
                p99s.append(v['lag_p99_ms'])
    if n:
        rows[shard] = (msgs / (n * 10.0), lw / msgs, sum(p99s) / len(p99s) if p99s else None, max(p99s) if p99s else None, n)
for s, (fps, p50, p99m, p99x, n) in sorted(rows.items()):
    print(f'{s:8s} fps {fps:8.1f}  p50 {p50:6.1f}  p99 mean {p99m if p99m is None else round(p99m, 1)} max {p99x}  windows {n}')
for kind in ('trade', 'book', 'mark'):
    v = [r[0] for s, r in rows.items() if s.startswith(kind)]
    if v:
        print(f'{kind}: shards {len(v)} fps min {min(v):.1f} max {max(v):.1f} max/min {max(v) / min(v):.2f} sum {sum(v):.1f}')
