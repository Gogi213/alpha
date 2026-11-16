#!/usr/bin/env python3
"""Реальные паузы шарда сделок Bybit: из events-<шард>.jsonl (gap_end: начало, длина) и файла сделок шарда — кадры вокруг паузы: приём (мс), время биржи (мс), лаг.
Запуск: real_pauses.py <корень> <шард> <день> <с epoch_s> — паузы с gap_start_ms ≥ метки."""
import glob, json, os, subprocess, sys

root, shard, day, since = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
gaps = []
for ln in open(os.path.join(root, f'events-{shard}.jsonl'), encoding='utf-8', errors='replace'):
    if '"ev":"gap_end"' in ln:
        v = json.loads(ln)
        if v['gap_start_ms'] >= since * 1000:
            gaps.append((v['gap_start_ms'], v['gap_total_ms']))
recs = []   # (rx_ms, e_ms, lag_ms, sym)
for f in sorted(glob.glob(os.path.join(root, day, f'{shard}-*.tsv.zst'))):
    out = subprocess.run(['zstd', '-dc', f], capture_output=True).stdout.decode('utf-8', 'replace')
    rx = 0
    for ln in out.split('\n'):
        p = ln.split('\t')
        if len(p) < 3:
            continue
        try:
            rx += int(p[1])
        except ValueError:
            continue
        if p[0] != 't' or len(p) < 8:
            continue
        try:
            lag = int(float(p[2]))
        except ValueError:
            continue
        recs.append((rx / 1e6, rx // 1_000_000 - lag, lag, p[4]))
recs.sort()
lags = sorted(r[2] for r in recs)
print('records', len(recs), 'lag p50/p99/p999', lags[len(lags) // 2], lags[int(len(lags) * .99)], lags[int(len(lags) * .999)])
for s, tot in gaps:
    before = [r for r in recs if r[0] <= s + 50][-3:]
    after = [r for r in recs if r[0] >= s + 50 and r[0] > before[-1][0]][:3] if before else []
    print('pause start', s, 'total_ms', tot, 'tick_end', s + tot)
    for tag, rr in (('before', before), ('after', after)):
        for r in rr:
            print(' ', tag, 'rx_ms %.1f' % r[0], 'e_ms', r[1], 'lag', r[2], r[3])
