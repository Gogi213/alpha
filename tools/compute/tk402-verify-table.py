#!/usr/bin/env python3
"""TK-402 A7: таблица verify по монето-суткам Bybit-фьюча (из verify-<SYM>.log/.status бинлогов TK-366): gaps, invariants, violations, статус монеты.
Сутки допускаются к счёту, если gaps=0 и invariants=0 (стакан цел); status=fail при gaps=0 invariants=0 — только доля нарушений СДЕЛОК, это пишется в графу reason.
Запуск (calc): python3 tk402-verify-table.py > /data/tk0402/verify.tsv"""
import os, re, glob
import sys
R = sys.argv[1] if len(sys.argv) > 1 else '/srv/alpha-sda/tk366w/bin-fut/e-2026-10'  # спот (FS): .../bin-spot/e-2026-10 > verify-spot.tsv
print('\t'.join(['sym', 'day', 'status', 'gaps', 'invariants', 'trades', 'violations', 'admit', 'reason']))
for lg in sorted(glob.glob(R + '/verify-*.log')):
    sym = os.path.basename(lg)[7:-4]
    try: st = open(lg[:-4] + '.status').read().split()[0]
    except Exception: st = 'none'
    for ln in open(lg):
        m = re.search(r'day=(\S+) .*gaps=(\d+) invariants=(\d+) trades=(\d+) out_of_range=(\d+) violations=(\d+)', ln)
        if not m: continue
        day, g, iv, tr, _, vi = m.groups(); g, iv, tr, vi = int(g), int(iv), int(tr), int(vi)
        adm = int(g == 0 and iv == 0)
        why = '' if adm else ('gaps=%d' % g if g else '') + (' invariants=%d' % iv if iv else '')
        if adm and st != 'ok': why = 'status=%s: нарушения сделок %d из %d, стакан цел' % (st, vi, tr)
        print('\t'.join([sym, day, st, str(g), str(iv), str(tr), str(vi), str(adm), why.strip()]))
