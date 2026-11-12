#!/usr/bin/env python3
"""TK-392: свод кривых по монетам. Запуск: tk392-agg.py CURVEDIR OUT.csv. Вес монеты в среднем = число событий n (day=all); CI 95% — бутстрап по монетам (1000).
Колонки: mkt,X,W,dir,h,coins,n,dex_move_bp,gate_resp_bp,ctrl_bp,excess_bp,share,share_lo,share_hi,dex_after_bp"""
import csv, glob, sys, collections, numpy as np
A = collections.defaultdict(list)
for fn in glob.glob(sys.argv[1] + '/*-curve.csv'):
    for r in csv.DictReader(open(fn)):
        if r['day'] != 'all' or r['gate_resp_bp'] == '': continue
        A[(r['mkt'], float(r['X']), int(r['W']), r['dir'], int(r['h']))].append(
            (r['coin'], int(r['n']), float(r['dex_move_bp']), float(r['gate_resp_bp']), float(r['ctrl_gate_bp'] or 0), float(r['dex_after_bp'] or 0)))
rng = np.random.default_rng(392)
out = []
for k in sorted(A):
    v = np.array([x[1:] for x in A[k]], float); n = v[:, 0]
    def st(idx):
        w = n[idx]; s = w.sum()
        return (w * v[idx, 1]).sum() / s, (w * v[idx, 2]).sum() / s, (w * v[idx, 3]).sum() / s, (w * v[idx, 4]).sum() / s
    allidx = np.arange(len(v)); dm, g, c, da = st(allidx)
    bs = []
    for _ in range(1000):
        i = rng.integers(0, len(v), len(v)); d_, g_, _, _ = st(i); bs.append(g_ / d_)
    out.append((*k[:4], k[4], len(v), int(n.sum()), round(dm, 1), round(g, 1), round(c, 1), round(g - c, 1), round(g / dm, 3),
                round(float(np.percentile(bs, 2.5)), 3), round(float(np.percentile(bs, 97.5)), 3), round(da, 1)))
with open(sys.argv[2], 'w', newline='') as f:
    w = csv.writer(f); w.writerow('mkt X W dir h coins n dex_move_bp gate_resp_bp ctrl_bp excess_bp share share_lo share_hi dex_after_bp'.split()); w.writerows(out)
print(len(out), 'rows')
