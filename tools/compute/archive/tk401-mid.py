#!/usr/bin/env python3
"""TK-401: ряды середины DEX (цена исполнения |q|/|t|, v2/v3/v4) ТОЛЬКО для НОВЫХ пулов TK-393 (pools-new.csv) и монет без кривой в TK-392 (дельта, В-213).
Запуск на calc: tk401-mid.py OUTDIR -> OUTDIR/pick.csv (coin,chain,pool,kind,leg,liq,n), OUTDIR/skip.csv (coin,why), OUTDIR/mid/COIN.csv.gz (ts,block,logidx,price), OUTDIR/list.txt (монета и нога).
Главный пул монеты = наибольшая liq среди новых пулов с известной квотой и >=50 свопов. Свопы всех каталогов sqd*/ дедуплицируются по (пул, блок, logidx). Смещение ±½ комиссии не поправлено (как слой v2/v4 TK-392)."""
import csv, glob, gzip, os, sys, collections
out = sys.argv[1]; os.makedirs(out + '/mid', exist_ok=True)
Z = '0x' + '0' * 40; K = '/data/tk0380/'; T = '/data/tk0393/'
Q = {'bsc': {'USDT': ('0x55d398326f99059ff775485246999027b3197955', ''), 'USDC': ('0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d', ''), 'BUSD': ('0xe9e7cea3dedca5984780bafc599bd69add087d56', ''), 'WBNB': ('0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c', 'BNB'), 'USD1': ('0x8d0d000ee44948fc98c9b98a4fa4921476f08b0d', '')},
     'ethereum': {'USDT': ('0xdac17f958d2ee523a2206206994597c13d831ec7', ''), 'USDC': ('0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48', ''), 'USDS': ('0xdc035d45d973e3ec169d2276ddab16f1e407384f', ''), 'WETH': ('0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2', 'ETH'), 'ETH': (Z, 'ETH')},
     'base': {'USDC': ('0x833589fcd6edb6e08f4c7c32d4f71b54bda02913', ''), 'WETH': ('0x4200000000000000000000000000000000000006', 'ETH'), 'ETH': (Z, 'ETH')},
     'polygon': {'USDC': ('0x3c499c542cef5e3811e1192ce70d8cc03d5c3359', ''), 'USDT': ('0xc2132d05d31c914a87c6611c10748aeb04b58e8f', ''), 'USDT0': ('0xc2132d05d31c914a87c6611c10748aeb04b58e8f', ''), 'WETH': ('0x7ceb23fd6bc0add59e62ac25578270cff1b9f619', 'ETH')},
     'avalanche': {'WAVAX': ('0xb31f66aa3c1e785363f0875a1b74e27b85fd66c7', 'AVAX'), 'USDC': ('0xb97ef9ef8734c71904d8002f8b6bc66dd9c48a6e', ''), 'USDT': ('0x9702230a8ea53601f5cd2dc00fdbc13d4df4a8c7', '')},
     'optimism': {'WETH': ('0x4200000000000000000000000000000000000006', 'ETH'), 'USDC': ('0x0b2c639c533813f4aa9d7837caf62653d097ff85', ''), 'USDT': ('0x94b008aa00579c1307b0ef2c499ad98a8ce58e58', '')},
     'arbitrum': {'WETH': ('0x82af49447d8a07e3bd95bd0d56f35241523fbab1', 'ETH'), 'USDC': ('0xaf88d065e77c8cc2239327c5edb3a432268e5831', ''), 'USDT': ('0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9', '')}}
done = set()
for d in ('curve', 'curve2', 'curve3', 'curve4', 'curve5'):
    p = K + 'tk392/' + d
    if os.path.isdir(p): done |= {f.split('-curve')[0] for f in os.listdir(p) if f.endswith('-curve.csv')}
new = {}; skip = {}
for r in csv.DictReader(open(T + 'pools-new.csv')):
    c = r['coin']
    if c in done: continue
    if r['chain'] not in Q or r['quote'] not in Q[r['chain']]:
        skip.setdefault(c, f"{r['chain']}/{r['labels']}/{r['quote']}: нет USD-квоты или чейн без свопов"); continue
    if not any(os.path.isdir(f'/srv/alpha-sda/tk372-gate/raw/{k}/{c}_USDT') for k in ('spot', 'fut')) and not os.path.exists(f'{K}bbo/{c}.csv.gz'):
        skip.setdefault(c, 'нет данных Gate'); continue
    try: liq = float(r['liq'])
    except ValueError: liq = 0
    new[(r['chain'], r['pool'].lower())] = (c, liq, r)
rows = collections.defaultdict(list); seen = set(); kinds = {}
for p in sorted(glob.glob(T + 'sqd*/*/swaps*.csv.gz')):
    ch = os.path.basename(os.path.dirname(p)).replace('-swaps', '').replace('-v4', '')
    try:
        for r in csv.DictReader(gzip.open(p, 'rt')):
            k = (ch, r['pool'].lower())
            if k not in new: continue
            key = (k, r['block'], r['logidx'])
            if key in seen: continue
            seen.add(key); kinds[k] = r['kind']
            a0 = abs(int(r['amount0'])); a1 = abs(int(r['amount1']))
            if a0 == 0 or a1 == 0: continue
            c, liq, m = new[k]; qa = Q[m['chain']][m['quote']][0]; q0 = qa < m['token'].lower()
            rows[k].append((int(r['ts']), int(r['block']), int(r['logidx']), (a0 / a1) if q0 else (a1 / a0)))
    except EOFError:
        print('ОБРЫВ gz (читаем до обрыва):', p)
best = {}
for k, v in rows.items():
    c, liq, m = new[k]; n = len(v)
    if kinds[k] not in ('v2', 'v3', 'v4'): skip.setdefault(c, f'{k[0]}: тип пула {kinds[k]}'); continue
    if n < 50: skip.setdefault(c, f'{k[0]}: свопов {n} < 50'); continue
    if c not in best or liq > best[c][0]: best[c] = (liq, k, n)
for c in {x[0] for x in new.values()} - set(best): skip.setdefault(c, 'нет свопов в скачанном')
lst = []
with open(out + '/pick.csv', 'w', newline='') as f:
    w = csv.writer(f); w.writerow('coin chain pool kind leg liq n'.split())
    for c, (liq, k, n) in sorted(best.items()):
        m = new[k][2]; leg = Q[m['chain']][m['quote']][1]; rr = sorted(rows[k])
        with gzip.open(f'{out}/mid/{c}.csv.gz', 'wt') as g:
            for ts, b, i, p in rr: g.write(f'{ts},{b},{i},{p:.12g}\n')
        w.writerow([c, k[0], k[1], kinds[k], leg, liq, n]); lst.append(f'{c} {leg}')
open(out + '/list.txt', 'w').write('\n'.join(lst) + '\n')
with open(out + '/skip.csv', 'w', newline='') as f:
    w = csv.writer(f); w.writerow(['coin', 'why']); w.writerows(sorted(skip.items()))
print(len(best), 'монет с рядом;', len(skip), 'пропусков;', len(done), 'уже в TK-392')
