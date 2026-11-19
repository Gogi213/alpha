#!/usr/bin/env python3
"""BingX: признаки нашей нагрузки/обратного давления по шардам сделок и книги до/после смены раскладки (stats-<шард>.jsonl).
Запуск: bx_bp.py <корень>   (до: 16.11 02..06 UTC, после: 16.11 08..10 UTC)"""
import glob, json, os, statistics, sys, time

def epoch(s):
    return int(time.mktime(time.strptime(s, '%Y-%m-%d %H:%M')) - time.timezone) * 1000

root = sys.argv[1]
B0, B1 = epoch('2026-11-16 02:00'), epoch('2026-11-16 07:00')
A0, A1 = epoch('2026-11-16 08:00'), epoch('2026-11-16 11:00')
F = ('cpu_frac', 'recvq_max', 'recvq_behind', 'busy_max_us', 'handoff_max_us', 'ring_depth_max', 'ring_full', 'queue_lag_p99_ms', 'lag_p99_ms', 'disk_write_max_us')
def stat(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return '-'
    vals.sort()
    return 'avg %.3g p99 %.3g max %.3g' % (sum(vals) / len(vals), vals[int(len(vals) * 0.99) - 1 if len(vals) > 100 else -1], vals[-1])
for f in sorted(glob.glob(os.path.join(root, 'stats-*.jsonl'))):
    shard = os.path.basename(f)[6:-6]
    if shard.startswith('mark'):
        continue
    d = {'до': {k: [] for k in F}, 'после': {k: [] for k in F}}
    with open(f, 'rb') as fh:
        fh.seek(max(0, os.path.getsize(f) - 60_000_000))
        for ln in fh:
            try:
                v = json.loads(ln)
            except ValueError:
                continue
            ts = v.get('ts_ms') or 0
            per = 'до' if B0 <= ts < B1 else 'после' if A0 <= ts < A1 else None
            if per:
                for k in F:
                    d[per][k].append(v.get(k))
    print('==', shard)
    for k in F:
        print('  %-18s до: %-40s после: %s' % (k, stat(d['до'][k]), stat(d['после'][k])))
