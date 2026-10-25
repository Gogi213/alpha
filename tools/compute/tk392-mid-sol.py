#!/usr/bin/env python3
"""TK-392 слой Solana (приближение): цена исполнения свопа price_usd из /data/tk0380/solout/swaps-usd.csv (TK-380 шаг 4; ножка WSOL/USDC уже в $)
для Gate-монет без EVM-пула в pick/pick2/pick4. Чистка как в tk380-sol-lag3: usd>=5, выброс |p/медиана ±30 соседей - 1|>10 %. Монеты с медианой в 0,98-1,02 $ (стейблы) и < 200 свопов — пропуск.
Запуск на calc: tk392-mid-sol.py OUTDIR -> OUTDIR/mid5/COIN.csv.gz (ts,slot,txidx,price), OUTDIR/list5.txt. Смещение ±½ комиссии пула не поправлено (пометка в отчёте)."""
import csv, gzip, os, sys, collections, statistics
out = sys.argv[1]; os.makedirs(out + '/mid5', exist_ok=True); K = '/data/tk0380/'
have = set()
for f in ('pick.csv', 'pick2.csv', 'pick4.csv'):
    p = f'{out}/{f}'
    if os.path.exists(p): have |= {r['coin'] for r in csv.DictReader(open(p))}
SW = collections.defaultdict(list)
for r in csv.DictReader(open(K + 'solout/swaps-usd.csv')):
    try: px = float(r['price_usd']); usd = float(r['usd'])
    except ValueError: continue
    if usd < 5 or px <= 0 or r['coin'] in have: continue
    SW[r['coin']].append((int(r['ts']), int(r['slot']), int(r['txidx']), px))
n = 0; lst = []
for c, rows in SW.items():
    if len(rows) < 200: continue
    if not (os.path.exists(f'{K}bbo/{c}.csv.gz') or os.path.isdir(f'/srv/alpha-sda/tk372-gate/raw/spot/{c}_USDT')): continue
    rows.sort(); ps = [r[3] for r in rows]
    if 0.98 <= statistics.median(ps) <= 1.02: continue
    keep = []
    for i, r in enumerate(rows):
        nb = ps[max(0, i - 30):i] + ps[i + 1:i + 31]
        if nb and abs(r[3] / statistics.median(nb) - 1) > 0.1: continue
        keep.append(r)
    with gzip.open(f'{out}/mid5/{c}.csv.gz', 'wt') as f:
        for ts, s, t, p in keep: f.write(f'{ts},{s},{t},{p:.12g}\n')
    lst.append(c); n += 1
open(out + '/list5.txt', 'w').write(''.join(f'{c} \n' for c in lst))
print(n, 'монет Solana')
