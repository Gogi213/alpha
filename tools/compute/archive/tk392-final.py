#!/usr/bin/env python3
"""TK-392: t50/t90 догона и Холм по ячейкам. Запуск: tk392-final.py CURVEDIR OUT_T.csv OUT_HOLM.csv
t50/t90 — первый горизонт h, где доля (ход Gate/ход DEX, вес = события) >= 0,5/0,9, иначе пусто; по ячейке mkt,X,W,dir.
Холм: проверяемые ячейки mkt x X x W x dir x h{60,300,900}; p — бутстрап по монетам (2000), двусторонний, по excess = ход Gate - контроль;
нулевая гипотеза excess = 0 (бутстрап центрирован). Поправка Холма по всему семейству."""
import csv, glob, sys, collections, numpy as np
A = collections.defaultdict(lambda: collections.defaultdict(list))
for fn in glob.glob(sys.argv[1] + '/*-curve.csv'):
    for r in csv.DictReader(open(fn)):
        if r['day'] != 'all' or r['gate_resp_bp'] == '': continue
        A[(r['mkt'], float(r['X']), int(r['W']), r['dir'])][int(r['h'])].append(
            (r['coin'], int(r['n']), float(r['dex_move_bp']), float(r['gate_resp_bp']), float(r['ctrl_gate_bp'] or 0)))
rng = np.random.default_rng(392)
T = []; H = []
for k in sorted(A):
    t = {0.5: '', 0.9: ''}
    for h in sorted(A[k]):
        v = np.array([x[1:] for x in A[k][h]], float); n = v[:, 0]
        sh = (n * v[:, 2]).sum() / (n * v[:, 1]).sum()
        for q in t:
            if t[q] == '' and sh >= q: t[q] = h
    T.append((*k, t[0.5], t[0.9]))
    for h in (60, 300, 900):
        if h not in A[k]: continue
        v = np.array([x[1:] for x in A[k][h]], float); n = v[:, 0]
        ex = v[:, 2] - v[:, 3]
        obs = (n * ex).sum() / n.sum()
        bs = []
        for _ in range(2000):
            i = rng.integers(0, len(v), len(v)); bs.append((n[i] * ex[i]).sum() / n[i].sum())
        bs = np.array(bs); p = max(2 * min((bs - bs.mean() >= obs).mean(), (bs - bs.mean() <= obs).mean()), 1 / 2000)
        H.append([*k, h, len(v), int(n.sum()), round(obs, 2), round(float(np.percentile(bs, 2.5)), 2), round(float(np.percentile(bs, 97.5)), 2), p])
order = np.argsort([x[-1] for x in H]); m = len(H); prev = 0; adj = {}
for rank, i in enumerate(order):
    prev = max(prev, min(1.0, (m - rank) * H[i][-1])); adj[i] = prev
with open(sys.argv[2], 'w', newline='') as f:
    w = csv.writer(f); w.writerow('mkt X W dir t50_s t90_s'.split()); w.writerows(T)
with open(sys.argv[3], 'w', newline='') as f:
    w = csv.writer(f); w.writerow('mkt X W dir h coins n excess_bp ex_lo ex_hi p p_holm'.split())
    for i, r in enumerate(H): w.writerow([*r[:-1], round(r[-1], 4), round(adj[i], 4)])
print(len(T), 'cells', m, 'tests; holm<0.05:', sum(1 for i in adj if adj[i] < 0.05))
