#!/usr/bin/env python3
"""TK-392: срезы отклика (сеть, ликвидность пула, класс, оборот, сутки). Запуск: tk392-slices.py CURVEDIR pick.csv participating.csv OUT.csv
Ячейка X=0,01 W=60; ключевые h; вес = n событий; CI 95% бутстрап по монетам (1000). Колонки: slice,value,mkt,dir,h,coins,n,dex_move_bp,gate_resp_bp,excess_bp,share,share_lo,share_hi"""
import csv, glob, sys, collections, numpy as np
cur, pick, part, out = sys.argv[1:5]
P = {r['coin']: r for r in csv.DictReader(open(pick))}
Q = {r['монета']: r for r in csv.DictReader(open(part, encoding='utf-8-sig'), delimiter=';')}
liqs = np.array([float(P[c]['liq']) for c in P]); lq = np.percentile(liqs, [33.3, 66.7])
turn = np.array([float(Q[c]['оборот_фьюч_gate_24ч_usd']) for c in P if c in Q and Q[c]['оборот_фьюч_gate_24ч_usd'] not in ('', '?')]); tq = np.percentile(turn, [33.3, 66.7])
def terc(x, q): return 'T1' if x < q[0] else ('T2' if x < q[1] else 'T3')
def cls(c): return {'да': 'на Binance/Bybit/OKX', 'нет': 'нет на крупных'}.get(Q.get(c, {}).get('есть_на_binance_bybit_okx', '?'), 'неизв.')
HS = {5, 60, 300, 900, 3600}
D = collections.defaultdict(lambda: collections.defaultdict(list))
for fn in glob.glob(cur + '/*-curve.csv'):
    for r in csv.DictReader(open(fn)):
        if float(r['X']) != 0.01 or int(r['W']) != 60 or int(r['h']) not in HS or r['gate_resp_bp'] == '': continue
        c = r['coin']
        if c not in P: continue
        tv = Q.get(c, {}).get('оборот_фьюч_gate_24ч_usd', '?')
        sl = [('все', 'все'), ('сеть', P[c]['chain']), ('ликв.пула', terc(float(P[c]['liq']), lq)), ('класс', cls(c)),
              ('оборот', terc(float(tv), tq) if tv not in ('', '?') else 'неизв.'), ('сутки', r['day'])]
        if r['day'] != 'all' and True: sl = [('сутки', r['day'])]
        else: sl = sl[:-1]
        for s in sl: D[(s, r['mkt'], r['dir'], int(r['h']))][c].append((int(r['n']), float(r['dex_move_bp']), float(r['gate_resp_bp']), float(r['ctrl_gate_bp'] or 0)))
rng = np.random.default_rng(392); rows = []
for k in sorted(D):
    cs = D[k]
    a = np.array([[sum(x[0] for x in v), sum(x[0] * x[1] for x in v), sum(x[0] * x[2] for x in v), sum(x[0] * x[3] for x in v)] for v in cs.values()], float)
    def st(i): s = a[i].sum(0); return s[1] / s[0], s[2] / s[0], s[3] / s[0]
    dm, g, c_ = st(np.arange(len(a))); bs = []
    for _ in range(1000):
        i = rng.integers(0, len(a), len(a)); d2, g2, _ = st(i); bs.append(g2 / d2)
    rows.append((k[0][0], k[0][1], k[1], k[2], k[3], len(a), int(a[:, 0].sum()), round(dm, 1), round(g, 1), round(g - c_, 1), round(g / dm, 3),
                 round(float(np.percentile(bs, 2.5)), 3), round(float(np.percentile(bs, 97.5)), 3)))
with open(out, 'w', newline='') as f:
    w = csv.writer(f); w.writerow('slice value mkt dir h coins n dex_move_bp gate_resp_bp excess_bp share share_lo share_hi'.split()); w.writerows(rows)
print(len(rows), 'rows')
