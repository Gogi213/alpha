#!/usr/bin/env python3
"""TK-388 / P-27 часть B: класс (EVM, mid=пул; COINS=список; недели 0..5 от 01.09) по эпизодам oosB/<COIN>/episodes-T*.csv. Бутстрап по монето-суткам (суммы/счётчики), Бонферрони на 96.
Запуск: tk388-classB.py <каталог oosB> <out.csv>. Тариф 3,25/1,3 уже в net; чувствительность VIP0 5/2 — сдвиг: a/b -3,5, c -2,45; ctrl -3,5."""
import sys, csv, glob, os, collections
import numpy as np
root, out = sys.argv[1], sys.argv[2]
rng = np.random.default_rng(388); R = 20000; ALPHA = 0.05 / 96
SH = {'a': -3.5, 'b': -3.5, 'c': -2.45}; SC = -3.5
cell = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0.0, 0.0, 0.0, 0.0]))  # (form,T,ex,H)->(coin,day)->[n,sum_net,sum_edge,sum_net_vip,sum_edge_vip]
coinc = collections.defaultdict(lambda: [0, 0.0, 0.0])  # (cellkey, coin)->n,sumnet,sumedge
wk = collections.defaultdict(lambda: [0, 0.0])           # (cellkey, week)
COINS = os.environ.get('COINS'); COINS = COINS.split(',') if COINS else None
for f in sorted(glob.glob(f'{root}/*/episodes-T*.csv')):
    for r in csv.DictReader(open(f)):
        if r['ctrl'] == '' or (COINS and r['coin'] not in COINS): continue
        coin = r['coin']; k = (r['form'], r['T'], r['ex'], r['H']); net = float(r['net']); ed = net - float(r['ctrl'])
        netv = net + SH[r['form']]; edv = ed + SH[r['form']] - SC
        c = cell[k][(coin, r['day'])]; c[0] += 1; c[1] += net; c[2] += ed; c[3] += netv; c[4] += edv
        cc = coinc[(k, coin)]; cc[0] += 1; cc[1] += net; cc[2] += ed
        w = wk[(k, int(r['dayidx']) // 7)]; w[0] += 1; w[1] += net
def lo(a, col, q):
    n = a[:, 0]; s = a[:, col]; idx = rng.integers(0, len(a), size=(R, len(a)))
    m = s[idx].sum(1) / n[idx].sum(1); return float(np.quantile(m, q))
res = []
for k, d in sorted(cell.items()):
    a = np.array(list(d.values())); n = int(a[:, 0].sum()); days = len(a)
    if n < 10 or days < 3: continue
    net, edge = a[:, 1].sum() / n, a[:, 2].sum() / n
    coins = [(c, v) for (kk, c), v in coinc.items() if kk == k and v[0] >= 30]
    cpos = sum(v[1] / v[0] > 0 for c, v in coins)
    ws = {w: wk[(k, w)] for w in range(6) if (k, w) in wk}
    res.append(dict(form=k[0], T=k[1], ex=k[2], H=k[3], n=n, cdays=days, ncoin30=len(coins), coins_pos=cpos, net=round(net, 1), edge=round(edge, 1),
                    net_lo95=round(lo(a, 1, 0.025), 1), edge_lo95=round(lo(a, 2, 0.025), 1), net_lo_bonf=round(lo(a, 1, ALPHA / 2), 1), edge_lo_bonf=round(lo(a, 2, ALPHA / 2), 1),
                    net_vip0=round(a[:, 3].sum() / n, 1), edge_lo_bonf_vip0=round(lo(a, 4, ALPHA / 2), 1),
                    **{f'w{i + 1}_{x}': (ws[i][0] if x == 'n' else round(ws[i][1] / ws[i][0], 1)) if i in ws else '' for i in range(6) for x in ('n', 'net')}))
    r = res[-1]; r['edge_yes'] = int(r['net'] > 0 and r['net_lo_bonf'] > 0 and r['edge_lo_bonf'] > 0 and cpos >= 2)
w = csv.DictWriter(open(out, 'w', newline=''), list(res[0])); w.writeheader(); w.writerows(res)
print('ячеек', len(res), 'net>0', sum(r['net'] > 0 for r in res), 'edge>0', sum(r['edge'] > 0 for r in res), 'edge_lo95>0', sum(r['edge_lo95'] > 0 for r in res), 'edge_yes', sum(r['edge_yes'] for r in res))
for r in sorted(res, key=lambda r: -r['edge'])[:8]: print(r)
