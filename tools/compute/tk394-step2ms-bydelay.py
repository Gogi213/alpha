#!/usr/bin/env python3
"""TK-394 шаг 2ms: разрез по задержке D (мс): клеток, эфф.N>=30, медианы gross/net, доля net>0, net>0 при >=5/7 дней. Запуск: STEP2MSDIR"""
import sys, os, json, glob
import numpy as np
FEE = {'GF': 5.0, 'BF': 5.5, 'BS': 10.0}; DISC = 0.65
d = sys.argv[1]; st = {}
for f in sorted(glob.glob(f'{d}/*.npz')):
    coin = os.path.basename(f)[:-4]; z = np.load(f); cells = json.load(open(f'{d}/{coin}.json'))
    S = z['sums'] + z['fs']; C = z['cnts'].astype(float); E = z['eps']
    for i, (pair, mode, anc, sc, k, Dl, T) in enumerate(cells):
        circ = 2 * (FEE['GF'] * (DISC if sc == 'disc' else 1.0) + (FEE['BF'] if pair == 'FF' else FEE['BS']))
        n = C[i].sum(); v = st.setdefault((pair, Dl), dict(all=0, ok=0, g=[], nt=[], pos=0, p5=0))
        v['all'] += 1
        if int(E[i].sum()) < 30: continue
        net = S[i] - C[i] * circ; m = net.sum() / n
        dn = net.reshape(7, 24).sum(1); dc = C[i].reshape(7, 24).sum(1)
        v['ok'] += 1; v['g'].append(z['sums'][i].sum() / n); v['nt'].append(m)
        v['pos'] += m > 0; v['p5'] += (m > 0) and int(np.sum((dn > 0) & (dc > 0))) >= 5
print('pair,delay_ms,cells,effN30,median_gross,median_net,net_pos,net_pos_5of7')
for (p, D), v in sorted(st.items()):
    mg = round(float(np.median(v['g'])), 2) if v['g'] else ''; mn = round(float(np.median(v['nt'])), 2) if v['nt'] else ''
    print(f"{p},{D},{v['all']},{v['ok']},{mg},{mn},{v['pos']},{v['p5']}")
