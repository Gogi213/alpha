#!/usr/bin/env python3
"""TK-410 П-31 (возврат Судьи 05:45 п.4): охват настоящей середины пула по монетам. P / H / G раздельно × источник × котировка.
Источник середины EVM: пул есть в summary.csv любого /data/tk0393/sqd*/<chain>-swaps (+ base-v4, ethereum-v4); иначе «нет в сырье».
Solana/Sui — отдельно (источник уточняется). Только чтение маленьких summary/CSV. Выход: stdout + OUT csv."""
import csv, glob, os, sys, collections
S = '/data/tk0393'; GEV = '/data/tk0402/gev2'; OUT = sys.argv[1] if len(sys.argv) > 1 else '/data/tk0393/tk410/cover.csv'
P_ = ['AGENCY', 'ALLINU', 'CLAUDIA', 'EMBER', 'FONE', 'GT', 'HOOKED', 'KIMCHI', 'LAPTOP', 'PAID', 'SI', 'STAMP', 'SUE', 'Q']
H_ = {'BTC', 'ETH', 'SOL', 'BNB', 'XRP', 'DOGE', 'ADA', 'AVAX', 'LINK', 'TRX', 'TON', 'DOT', 'LTC', 'BCH', 'MATIC', 'POL', 'USDC', 'USDT'}
STAB = ('USDT', 'USDC', 'USD', 'DAI', 'FDUSD', 'USDC.e', 'BUSD', 'USD1')
have = {}
for f in glob.glob(S + '/sqd*/*/summary.csv'):
    src = os.path.basename(os.path.dirname(f))
    for l in open(f).read().splitlines()[1:]:
        have.setdefault(l.split(',')[0].lower(), set()).add(f.split('/')[3] + '/' + src)
gdirs = set(os.listdir(GEV)); rows = []
for fn in (S + '/pools-new.csv', S + '/pools-old.csv', S + '/pools-solidly.csv'):
    if os.path.exists(fn): rows += list(csv.DictReader(open(fn)))
best = {}
for r in rows:
    if r['coin'] not in gdirs: continue
    if r['coin'] not in best or float(r['vol24'] or 0) > float(best[r['coin']]['vol24'] or 0): best[r['coin']] = r
c = collections.Counter(); w = csv.writer(open(OUT, 'w', newline=''))
w.writerow('coin,class,chain,pool,dex,labels,quote,quote_kind,source,vol24'.split(','))
for coin, r in sorted(best.items()):
    cl = 'P' if coin in P_ else ('H' if coin in H_ else 'G')
    ch = r['chain']
    if ch in ('solana', 'sui', 'tron'): src = 'не-EVM:' + ch
    else: src = ('сырьё: ' + ','.join(sorted(have[r['pool'].lower()]))) if r['pool'].lower() in have else 'нет в сырье'
    qk = 'стейбл' if r['quote'] in STAB else ('кот. с книгой Gate' if r['quote'].lstrip('W') in gdirs else 'кот. без книги Gate')
    w.writerow([coin, cl, ch, r['pool'], r['dex'], r['labels'], r['quote'], qk, src, r['vol24']])
    c[(cl, 'есть' if src.startswith('сырьё') else src.split(' ')[0].split(':')[0] if src.startswith('не-EVM') else 'нет', ch if ch in ('solana','sui','tron') else 'evm', qk)] += 1
for k, n in sorted(c.items()): print(k, n)
print('P поимённо:'); [print(' ', coin, best[coin]['chain'], best[coin]['labels'], best[coin]['quote']) for coin in P_ if coin in best]
print('P без пула/книги gev2:', [x for x in P_ if x not in best])
