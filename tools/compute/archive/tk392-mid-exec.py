#!/usr/bin/env python3
"""TK-392 слой «приближение»: v2/v4 пулы монет без v3-пула (нет в pick.csv/pick2.csv) — цена исполнения свопа |q|/|t| вместо середины.
Запуск на calc: tk392-mid-exec.py OUTDIR  -> OUTDIR/pick4.csv (coin,chain,pool,leg,liq) и OUTDIR/mid4/COIN.csv.gz (ts,block,logidx,price).
Главный пул монеты = наибольшая liq среди v2/v4 с квотой из списка Q; цена в сырых единицах (десятичные константны в пуле, события относительные).
Смещение: цена исполнения отличается от середины на ±½ комиссии (v2 0,25-0,3 %, v4 по тиру) — поправки нет, пометка в отчёте (спецификация TK-384 п.3)."""
import csv, gzip, os, sys, collections
out = sys.argv[1]; os.makedirs(out + '/mid4', exist_ok=True)
Z = '0x' + '0' * 40
Q = {'bsc': {'USDT': ('0x55d398326f99059ff775485246999027b3197955', ''), 'USDC': ('0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d', ''),
             'BUSD': ('0xe9e7cea3dedca5984780bafc599bd69add087d56', ''), 'WBNB': ('0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c', 'BNB')},
     'ethereum': {'USDT': ('0xdac17f958d2ee523a2206206994597c13d831ec7', ''), 'USDC': ('0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48', ''),
                  'USDS': ('0xdc035d45d973e3ec169d2276ddab16f1e407384f', ''), 'WETH': ('0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2', 'ETH'), 'ETH': (Z, 'ETH')},
     'base': {'USDC': ('0x833589fcd6edb6e08f4c7c32d4f71b54bda02913', ''), 'WETH': ('0x4200000000000000000000000000000000000006', 'ETH'), 'ETH': (Z, 'ETH')}}
K = '/data/tk0380/'
have = set()
for f in ('pick.csv', 'pick2.csv'):
    have |= {r['coin'] for r in csv.DictReader(open(K + 'tk392/' + f))}
best = {}
for r in csv.DictReader(open(K + 'pools-all.csv')):
    c = r['coin']; ch = r['chain']
    if r['labels'] not in ('v2', 'v4') or c in have or ch not in Q or r['quote'] not in Q[ch]: continue
    if not (os.path.exists(f'{K}bbo/{c}.csv.gz') or os.path.isdir(f'/srv/alpha-sda/tk372-gate/raw/spot/{c}_USDT')): continue
    try: liq = float(r['liq'])
    except ValueError: liq = 0
    if c not in best or liq > best[c][0]: best[c] = (liq, r)
P = {}; meta = []
for c, (liq, r) in best.items():
    qa, leg = Q[r['chain']][r['quote']]; t = r['token'].lower()
    P[(r['chain'], r['pool'].lower())] = (c, qa < t)   # квота = token0, если адрес меньше (v4 native ETH = 0x0 -> token0)
    meta.append((c, r['chain'], r['pool'], leg, liq))
with open(out + '/pick4.csv', 'w', newline='') as f:
    w = csv.writer(f); w.writerow('coin chain pool leg liq'.split()); w.writerows(sorted(meta))
rows = collections.defaultdict(list)
for ch, fn in [('bsc', 'bsc-swaps'), ('ethereum', 'ethereum-swaps'), ('ethereum', 'ethereum-v4'), ('base', 'base-swaps'), ('base', 'base-v4')]:
    p = f'{K}sqd/{fn}/swaps.csv.gz'
    if not os.path.exists(p): print('нет', p); continue
    for r in csv.DictReader(gzip.open(p, 'rt')):
        k = (ch, r['pool'].lower())
        if k not in P: continue
        a0 = abs(int(r['amount0'])); a1 = abs(int(r['amount1']))
        if a0 == 0 or a1 == 0: continue
        c, q0 = P[k]
        rows[c].append((int(r['ts']), int(r['block']), int(r['logidx']), (a0 / a1) if q0 else (a1 / a0)))
for c, rr in rows.items():
    rr.sort()
    with gzip.open(f'{out}/mid4/{c}.csv.gz', 'wt') as f:
        for ts, b, i, m in rr: f.write(f'{ts},{b},{i},{m:.12g}\n')
print(len(meta), 'пулов', len(rows), 'монет со свопами')
