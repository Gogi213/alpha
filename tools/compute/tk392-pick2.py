#!/usr/bin/env python3
"""TK-392: главный v3-пул монеты с квотой WBNB (bsc) / WETH (ethereum, base) для монет без стейбл-пула (нет в pick.csv). Запуск на calc: tk392-pick2.py OUT.csv"""
import csv, os, sys
Q = {('bsc', 'WBNB'): ('0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c', 'BNB'), ('ethereum', 'WETH'): ('0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2', 'ETH'),
     ('base', 'WETH'): ('0x4200000000000000000000000000000000000006', 'ETH')}
have = {r['coin'] for r in csv.DictReader(open('/data/tk0380/tk392/pick.csv'))}
best = {}
for r in csv.DictReader(open('/data/tk0380/pools-all.csv')):
    k = (r['chain'], r['quote']); c = r['coin']
    if r['labels'] != 'v3' or k not in Q or c in have: continue
    if not (os.path.exists(f'/data/tk0380/bbo/{c}.csv.gz') or os.path.isdir(f'/srv/alpha-sda/tk372-gate/raw/spot/{c}_USDT')): continue
    try: liq = float(r['liq'])
    except ValueError: liq = 0
    if c not in best or liq > best[c][0]: best[c] = (liq, r, Q[k])
with open(sys.argv[1], 'w', newline='') as f:
    w = csv.writer(f); w.writerow('coin chain pool token quote_addr liq leg'.split())
    for c, (liq, r, (q, leg)) in sorted(best.items()): w.writerow([c, r['chain'], r['pool'], r['token'], q, liq, leg])
print(len(best), 'coins', sys.argv[1])
