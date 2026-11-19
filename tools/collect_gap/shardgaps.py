#!/usr/bin/env python3
"""Дыры потока по шардам из stats-<шард>.jsonl коллектора (окно 10 с: msgs, max_gap_ms) — «шард целиком молчал».

max_gap_ms — наибольший промежуток между записями потока шарда за окно (все символы шарда вместе); окно с msgs = 0 или
пропуск окон в файле статистики — шард молчал всё окно. Это счётчик самого коллектора (по правилу 13 он не доказывает полноту),
но по сути это разность приёма подряд идущих записей файла — поэтому годится как проверка «соединение целиком упало».
Запуск: python3 shardgaps.py --root <данные биржи> --out <файл.json> [--t-start epoch]
"""
import argparse, glob, json, os, time

ap = argparse.ArgumentParser()
ap.add_argument('--root', required=True)
ap.add_argument('--out', required=True)
ap.add_argument('--t-start', type=int, default=1794528000)
a = ap.parse_args()
res = {}
for f in sorted(glob.glob(os.path.join(a.root, 'stats-*.jsonl'))):
    shard = os.path.basename(f)[6:-6]
    n = 0
    prev = None
    big = []  # (ts, max_gap_ms, msgs)
    skipped = []  # (от, до) пропуск окон > 25 с
    zero = 0
    for line in open(f):
        try:
            d = json.loads(line)
        except Exception:
            continue
        t = d.get('ts_ms', 0) // 1000
        if t < a.t_start:
            continue
        n += 1
        if prev is not None and t - prev > 25:
            skipped.append((prev, t))
        prev = t
        if d.get('msgs', 1) == 0:
            zero += 1
        g = d.get('max_gap_ms', 0)
        if g >= 1000:
            big.append((t, g, d.get('msgs')))
    res[shard] = {
        'windows': n, 'windows_msgs0': zero,
        'gap_ge_1s': len(big), 'gap_ge_5s': sum(1 for b in big if b[1] >= 5000), 'gap_ge_30s': sum(1 for b in big if b[1] >= 30000),
        'stats_skipped_ge25s': len(skipped),
        'top': [{'t': time.strftime('%d.%m %H:%M:%S', time.gmtime(b[0])), 'max_gap_ms': b[1], 'msgs': b[2]} for b in sorted(big, key=lambda b: -b[1])[:5]],
        'skipped_top': [{'from': time.strftime('%d.%m %H:%M:%S', time.gmtime(s[0])), 'to': time.strftime('%d.%m %H:%M:%S', time.gmtime(s[1])), 's': s[1] - s[0]} for s in sorted(skipped, key=lambda s: s[0] - s[1])[:5]],
    }
json.dump(res, open(a.out, 'w'), ensure_ascii=False, indent=1)
for sh, r in res.items():
    if sh.startswith('book'):
        print(sh, {k: r[k] for k in ('windows', 'windows_msgs0', 'gap_ge_1s', 'gap_ge_5s', 'gap_ge_30s', 'stats_skipped_ge25s')})
