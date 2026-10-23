#!/usr/bin/env python3
"""TK-376 проход 6, сводка: (А) глубина v3 (depth.csv) — net = net_1 − газ закрытия сети (p50) − проскальзывание 1000 USD, по сетям и стратам open_gap, bootstrap по монето-суткам (400);
(Б) контроль «случайный момент» (ctrl_sw.csv bin) против эпизодов (episodes.csv) в тех же стратах, вес — одно наблюдение. Запуск: sum.py depth.csv episodes.csv ctrl_sw.csv out.csv"""
import sys, csv, random, collections
DEP, EP, CT, OUT = sys.argv[1:5]
GAS_BP = {'ethereum': 0.71, 'base': 0.059, 'bsc': 0.059}   # газ закрытия p50 USD (0,071; 0,0059; 0,0059) / 1000 USD * 1e4
random.seed(7)
def boot(cl, B=400):
    """cl: {key: (sum, n)} -> mean, lo, hi (ratio, bootstrap по кластерам)"""
    ks = list(cl); S = sum(v[0] for v in cl.values()); Nn = sum(v[1] for v in cl.values())
    if Nn == 0: return None, None, None
    ms = []
    for _ in range(B):
        s = n = 0
        for k in random.choices(ks, k=len(ks)): s += cl[k][0]; n += cl[k][1]
        if n: ms.append(s / n)
    ms.sort(); return S / Nn, ms[int(.025 * len(ms))], ms[int(.975 * len(ms)) - 1]
def q(xs, p):
    xs = sorted(xs); return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else None
out = []
def put(**kw): out.append(kw); print(kw)
# (А)
dep = list(csv.DictReader(open(DEP)))
for r in dep:
    for k in ('open_gap', 'dur_s', 'fee_bp', 'net_1', 'V_usd', 'slip_bp', 'move_bp', 'cross'): r[k] = float(r[k]) if r[k] != '' else None
    r['str'] = '50_100' if abs(r['open_gap']) < 100 else 'ge100'
for nets in (['ethereum'], ['base'], ['bsc'], ['ethereum', 'base', 'bsc']):
    for st in ('50_100', 'ge100'):
        for var in ('all', 'no_cross'):
            rs = [r for r in dep if r['net'] in nets and r['str'] == st and r['net_1'] is not None and (var == 'all' or not r['cross'])]
            if not rs: continue
            def cl(f):
                d = collections.defaultdict(lambda: [0.0, 0])
                for r in rs: d[(r['net'], r['coin'], r['day'])][0] += f(r); d[(r['net'], r['coin'], r['day'])][1] += 1
                return {k: tuple(v) for k, v in d.items()}
            m1, a1, b1 = boot(cl(lambda r: r['net_1']))
            m2, a2, b2 = boot(cl(lambda r: r['net_1'] - GAS_BP[r['net']] - r['slip_bp']))
            put(part='depth', nets='+'.join(nets), strat=st, var=var, n=len(rs), coin_days=len({(r['net'], r['coin'], r['day']) for r in rs}),
                net1=round(m1, 2), net1_lo=round(a1, 2), net1_hi=round(b1, 2), slip_p50=round(q([r['slip_bp'] for r in rs], .5), 2), slip_p90=round(q([r['slip_bp'] for r in rs], .9), 2),
                V_p50=round(q([r['V_usd'] for r in rs], .5)), cross_share=round(sum(r['cross'] for r in rs) / len(rs), 3),
                net_adj=round(m2, 2), adj_lo=round(a2, 2), adj_hi=round(b2, 2), pos_adj=round(sum(1 for r in rs if r['net_1'] - GAS_BP[r['net']] - r['slip_bp'] > 0) / len(rs), 3))
# (Б)
def sbin(r):
    a = abs(float(r['open_gap'])); return 'fee_50' if a < 50 else '50_100' if a < 100 else 'ge100'
epc = collections.defaultdict(dict)
for r in csv.DictReader(open(EP)):
    if r['net_1'] == '': continue
    for scope in (r['net'], 'all'):
        d = epc[(scope, sbin(r))]; k = (r['net'], r['coin'], r['day']); s, n = d.get(k, (0.0, 0)); d[k] = (s + float(r['net_1']), n + 1)
ctc = collections.defaultdict(dict)
for r in csv.DictReader(open(CT)):
    for scope in (r['net'], 'all'):
        d = ctc[(scope, r['bin'])]; k = (r['net'], r['coin'], r['day'], r['dex']); s, n = d.get(k, (0.0, 0)); d[k] = (s + float(r['nsum']), n + int(r['n']))
for scope in ('ethereum', 'base', 'bsc', 'all'):
    for b in ('le_fee', 'fee_50', '50_100', 'ge100'):
        e = epc.get((scope, b)); c = ctc.get((scope, b))
        row = dict(part='ctrl', scope=scope, strat=b)
        if e: m, a, h = boot(e); row.update(ep_n=sum(v[1] for v in e.values()), ep_net1=round(m, 2), ep_lo=round(a, 2), ep_hi=round(h, 2))
        if c: m, a, h = boot(c); row.update(ct_n=sum(v[1] for v in c.values()), ct_net1=round(m, 2), ct_lo=round(a, 2), ct_hi=round(h, 2))
        put(**row)
keys = sorted({k for r in out for k in r})
with open(OUT, 'w', newline='') as f:
    w = csv.DictWriter(f, keys); w.writeheader(); w.writerows(out)
