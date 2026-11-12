#!/usr/bin/env python3
"""TK-392: выбор главного v3-пула монеты (стейбл-квота, наибольшая ликвидность) среди монет с данными Gate. Запуск на calc: tk392-pick.py OUT.csv"""
import csv, os, sys
Q = {('bsc', 'USDT'): '0x55d398326f99059ff775485246999027b3197955', ('bsc', 'USDC'): '0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d',
     ('bsc', 'USD1'): '0x8d0d000ee44948fc98c9b98a4fa4921476f08b0d', ('ethereum', 'USDT'): '0xdac17f958d2ee523a2206206994597c13d831ec7',
     ('ethereum', 'USDC'): '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48', ('base', 'USDC'): '0x833589fcd6edb6e08f4c7c32d4f71b54bda02913'}
best = {}
for r in csv.DictReader(open('/data/tk0380/pools-all.csv')):
    k = (r['chain'], r['quote'])
    if r['labels'] != 'v3' or k not in Q: continue
    c = r['coin']
    if not (os.path.exists(f'/data/tk0380/bbo/{c}.csv.gz') or os.path.isdir(f'/srv/alpha-sda/tk372-gate/raw/spot/{c}_USDT')): continue
    try: liq = float(r['liq'])
    except ValueError: liq = 0
    if c not in best or liq > best[c][0]: best[c] = (liq, r, Q[k])
with open(sys.argv[1], 'w', newline='') as f:
    w = csv.writer(f); w.writerow('coin chain pool token quote_addr liq'.split())
    for c, (liq, r, q) in sorted(best.items()): w.writerow([c, r['chain'], r['pool'], r['token'], q, liq])
print(len(best), 'coins', sys.argv[1])
