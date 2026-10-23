#!/usr/bin/env python3
"""TK-364 шаг 3: свод raw.json псевдотеста -> csv (группа, клетка, горизонт, день) + md. usage: tk364-pseudotest-agg.py raw.json OUT_PREFIX"""
import sys, json, math, random, collections
raw = json.load(open(sys.argv[1])); pre = sys.argv[2]
BSC_BAD = {4, 5, 6}          # BSC 19–21.10: охват пулов < 50 % (дыра скачивания) — исключены
FAM = 54; Z = 3.09           # Бонферрони 0.05/54 ≈ 0.001 односторонне
cell = collections.defaultdict(lambda: [0, 0.0, 0.0, 0.0, 0.0, 0])      # (g,x,T,h,dd) -> суммы
coin = collections.defaultdict(lambda: [0, 0.0])                         # (g,x,T,h,coin) -> n, sum diff
cd = collections.defaultdict(lambda: [0, 0.0])                           # (g,x,T,h,coin,dd) кластеры
status = collections.Counter(); groups_coins = collections.defaultdict(set)
for r in raw:
    status[r['status'].split('(')[0]] += 1
    if r['status'] != 'ok' and not r['status'].startswith('price_mismatch'): continue
    for k, v in r.get('cells', {}).items():
        x, T, h, dd = map(int, k.split('|'))
        if r['net'] == 'bsc' and dd in BSC_BAD: continue
        g = r['group']
        c = cell[(g, x, T, h, dd)]
        for i in range(6): c[i] += v[i]
        q = coin[(g, x, T, h, r['coin'])]; q[0] += v[0]; q[1] += v[4]
        z = cd[(g, x, T, h, r['coin'], dd)]; z[0] += v[0]; z[1] += v[4]
        groups_coins[g].add(r['coin'])
rows = []
for (g, x, T, h, dd), c in sorted(cell.items()):
    n = c[0]
    rows.append((g, x, T, h, dd + 15, n, c[1] / n, c[2] / n, c[3] / n, c[4] / n, c[5] / n))
with open(pre + '.csv', 'w') as f:
    f.write('group,X_bp,T_s,horizon_s,day,events,mean_ev_bp,mean_ctl_bp,mean_pre_bp,mean_diff_bp,follow_share\n')
    for r in rows: f.write(','.join(f'{v:.3f}' if isinstance(v, float) else str(v) for v in r) + '\n')
rnd = random.Random(1)
out = []
keys = sorted({(g, x, T, h) for (g, x, T, h, _) in cell})
for g, x, T, h in keys:
    days = [(dd, cell[(g, x, T, h, dd)]) for dd in range(7) if (g, x, T, h, dd) in cell]
    N = sum(c[0] for _, c in days)
    if N == 0: continue
    S = lambda i: sum(c[i] for _, c in days)
    m = S(4) / N
    cl = [(n, d) for (gg, xx, TT, hh, cn, dd), (n, d) in cd.items() if (gg, xx, TT, hh) == (g, x, T, h)]
    se = math.sqrt(sum((d - m * n) ** 2 for n, d in cl)) / N
    cns = {k[4]: v for k, v in coin.items() if k[:4] == (g, x, T, h)}
    ncoin = len(cns); npos = sum(1 for v in cns.values() if v[1] > 0)
    maxshare = max(v[0] for v in cns.values()) / N
    dpos13 = sum(1 for _, c in days if c[0] and c[4] / c[0] > 13); dpos0 = sum(1 for _, c in days if c[0] and c[4] / c[0] > 0)
    out.append(dict(g=g, x=x, T=T, h=h, N=N, coins=ncoin, ev=S(1) / N, ctl=S(2) / N, pre=S(3) / N, diff=m, fol=S(5) / N, se=se,
                    lo=m - Z * se, days=len(days), d13=dpos13, d0=dpos0, npos=npos, share=maxshare))
for o in out:
    o['cand'] = (o['h'] >= 10 and o['diff'] > 13 and o['d13'] >= 5 and o['npos'] >= 10 and o['share'] <= 0.3 and o['lo'] > 0)
with open(pre + '.md', 'w') as f:
    f.write('# TK-364 шаг 3: псевдотест DEX → Bybit, 15–21.10.2026\n\n')
    f.write(f'Статусы монет: {dict(status)}. Монет в группах: ' + ', '.join(f'{g} {len(s)}' for g, s in sorted(groups_coins.items())) + '\n\n')
    f.write('Событие − контроль (бп, сторона DEX), среднее по событиям; SE кластерный по монето-суткам; lo = diff − 3,09·SE (Бонферрони 54, односторонне). '
            'd13 — число суток со средним > 13 бп; npos — монет с плюсом; share — доля макс. монеты.\n\n')
    f.write('| группа | X | T | h | N | монет | событие | контроль | предход | diff | следствие % | SE | lo | d13/дн | npos | share | кандидат |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n')
    for o in out:
        f.write(f"| {o['g']} | {o['x']} | {o['T']} | {o['h']} | {o['N']} | {o['coins']} | {o['ev']:+.1f} | {o['ctl']:+.1f} | {o['pre']:+.1f} | {o['diff']:+.1f} | {100*o['fol']:.0f} | {o['se']:.1f} | {o['lo']:+.1f} | {o['d13']}/{o['days']} | {o['npos']} | {o['share']:.2f} | {'ДА' if o['cand'] else ''} |\n")
    f.write(f"\nКандидатов: {sum(1 for o in out if o['cand'])} из {sum(1 for o in out if o['h'] >= 10)} клеток (h ≥ 10 с).\n")
print(open(pre + '.md').read()[:6000])
