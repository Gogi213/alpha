#!/usr/bin/env python3
"""TK-378: охват Solana-переводов по монетам: переводы, tx, сутки, vault-кандидаты (аккаунты, частые как источник/получатель mint).
Запуск: tk378-sol-count.py MINTS.csv sol-transfers.csv"""
import sys, csv, collections
coin = {r['mint']: (r['src'], r['coin']) for r in csv.DictReader(open(sys.argv[1]))}
tx = collections.defaultdict(set); n = collections.Counter(); days = collections.defaultdict(set)
acc = collections.defaultdict(collections.Counter); vol = collections.Counter()
for x in csv.DictReader(open(sys.argv[2])):
    m = x['mint']
    if m not in coin or not x['value']: continue
    n[m] += 1; tx[m].add(x['tx_signature']); days[m].add(x['block_timestamp'][:10])
    acc[m][x['source']] += 1; acc[m][x['destination']] += 1
    vol[m] += int(x['value']) / 10 ** int(float(x['decimals'] or 0))
print('src,coin,transfers,txs,days,top_acc_share,top2_acc_txs')
for m, (s, c) in sorted(coin.items(), key=lambda kv: kv[1]):
    t = acc[m].most_common(2); tot = max(n[m], 1)
    print(f"{s},{c},{n[m]},{len(tx[m])},{len(days[m])},{t[0][1]/tot if t else 0:.2f},{t[1][1] if len(t)>1 else 0}")
