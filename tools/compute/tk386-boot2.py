#!/usr/bin/env python3
"""TK-386: бутстрап по суткам с поправкой Бонферрони (1-0,05/96, двусторонний) + строка на монету.
Запуск: tk386-boot2.py out.csv in1.csv ...  (эпизоды tk386-*.csv; ячейка n>=10 и >=3 суток; 20000 повторов)"""
import sys, csv, random, statistics, collections
rng = random.Random(386); out = sys.argv[1]; rows = []
for f in sys.argv[2:]:
    tag = f.split('tk386-')[1].replace('-2026-10-24.csv', '')
    for r in csv.DictReader(open(f)):
        if r['ctrl'] != '': r['set'] = tag; rows.append(r)
cells = collections.defaultdict(list)
for r in rows: cells[(r['set'], r['coin'], r['form'], r['T'], r['ex'], r['H'])].append(r)
R = 20000; QA = 0.05 / 96 / 2
def ci(byd, q):
    days = list(byd); m = []
    for _ in range(R):
        s = []; [s.extend(byd[rng.choice(days)]) for _ in days]; m.append(sum(s) / len(s))
    m.sort(); return m[int(q * R)], m[int((1 - q) * R) - 1]
res = []
for k, v in sorted(cells.items()):
    if len(v) < 10: continue
    byd = collections.defaultdict(list)
    for r in v: byd[r['day']].append(float(r['net']) - float(r['ctrl']))
    if len(byd) < 3: continue
    bn = collections.defaultdict(list)
    for r in v: bn[r['day']].append(float(r['net']))
    n95 = ci(byd, 0.025); nb = ci(byd, QA); netlo = ci(bn, QA)
    net = statistics.mean(float(r['net']) for r in v); ed = statistics.mean(float(r['net']) - float(r['ctrl']) for r in v)
    res.append(dict(set=k[0], coin=k[1], form=k[2], T=k[3], ex=k[4], H=k[5], n=len(v), days=len(byd), net=round(net, 1),
                    edge=round(ed, 1), edge_lo95=round(n95[0], 1), edge_lo_bonf=round(nb[0], 1), net_lo_bonf=round(netlo[0], 1),
                    pass_all=int(net > 0 and nb[0] > 0 and len(v) >= 30)))
w = csv.DictWriter(open(out, 'w', newline=''), list(res[0])); w.writeheader(); w.writerows(res)
byc = collections.defaultdict(list)
for r in res: byc[(r['set'], r['coin'])].append(r)
print('set coin ячеек net>0 edge_lo95>0 edge_lo_bonf>0 pass_all | лучшая по edge')
for k, v in sorted(byc.items()):
    b = max(v, key=lambda r: r['edge'])
    print(k, len(v), sum(r['net'] > 0 for r in v), sum(r['edge_lo95'] > 0 for r in v), sum(r['edge_lo_bonf'] > 0 for r in v), sum(r['pass_all'] for r in v), '|', b['form'], b['T'], b['ex'], b['H'], 'n', b['n'], 'd', b['days'], 'net', b['net'], 'edge', b['edge'], b['edge_lo95'], b['edge_lo_bonf'])
