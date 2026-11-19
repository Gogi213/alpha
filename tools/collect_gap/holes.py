#!/usr/bin/env python3
"""Дыры книги окна по символам и по минутам (разбор `postcheck.py`): где именно торговые минуты без записи `b`.

Печатает: число дыр по минутам (UTC) — где всплески (обрыв соединения даёт много символов в одной минуте), число дыр по символам и их минуты.
Запуск: python3 holes.py --exchange bybit --dir /data/perf/poolfix-deploy/after60/bybit [--top 15]
"""
import argparse, collections, glob, json, os, time
import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument('--exchange', required=True)
ap.add_argument('--dir', required=True)
ap.add_argument('--top', type=int, default=15)
a = ap.parse_args()
u = json.load(open(os.path.join(a.dir, 'univ.json')))
univ = {s for s, v in u['qv'].items() if v >= u['min_qv']}
d = np.load(os.path.join(a.dir, 'agg', a.exchange + '.npz'))
am0 = int(d['minute0'][0])
asym = {str(s): i for i, s in enumerate(d['syms'])}
B = d['book']
nmin = B.shape[1]
K = {}
km0 = None
for p in sorted(glob.glob(os.path.join(a.dir, 'klines', f'{a.exchange}-klines-p*.npz'))):
    z = np.load(p)
    km0 = int(z['minute0'][0])
    for j, s in enumerate(z['syms']):
        K[str(s)] = z['vol'][j]
off = km0 - am0
by_min = collections.Counter()
by_sym = collections.defaultdict(list)
for s in sorted(univ):
    vol = K.get(s)
    if vol is None:
        continue
    b = B[asym[s]] if s in asym else np.zeros(nmin, dtype=np.int32)
    for m in range(1, nmin - 1):
        j = m + off
        if 0 <= j < len(vol) and vol[j] > 0 and b[m] == 0:
            by_min[am0 + m] += 1
            by_sym[s].append(am0 + m)
print('holes by minute (top):')
for m, c in by_min.most_common(a.top):
    print(' ', time.strftime('%H:%M', time.gmtime(m * 60)), c)
print('holes by symbol (top):')
for s, ms in sorted(by_sym.items(), key=lambda kv: -len(kv[1]))[:a.top]:
    print(' ', s, len(ms), ' '.join(time.strftime('%H:%M', time.gmtime(m * 60)) for m in ms[:8]))
print('symbols with holes:', len(by_sym), 'minutes with holes:', len(by_min), 'total holes:', sum(by_min.values()))
