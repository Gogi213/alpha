#!/usr/bin/env python3
"""TK-380: tx Helius (tk380-sol-gtfa.py, tx-<pool>.jsonl.gz) -> дельты токен-аккаунтов в формате dl/*.csv TK-378
(ts,slot,txidx,sig,payer,fee,cu,mint,account,delta,dec) для tk378-sol-swaps2.py. Запуск: tk380-sol-conv.py SOL_DIR OUT.csv
Порядок: (slot, idx), дубли sig (tx в двух пулах) пишутся один раз."""
import sys, os, gzip, json, csv, glob
rows = {}
for f in glob.glob(os.path.join(sys.argv[1], 'tx-*.jsonl.gz')):
    for line in gzip.open(f, 'rt'):
        x = json.loads(line)
        if x['s'] in rows: continue
        pre = {b[0]: b for b in x['pre']}; post = {b[0]: b for b in x['post']}
        out = []
        for i in set(pre) | set(post):
            b = post.get(i) or pre[i]
            d = int(post[i][3] if i in post else 0) - int(pre[i][3] if i in pre else 0)
            if d: out.append((b[1], b[2] or ('i%d' % i), d, b[4]))  # account = owner (ALT-адреса в compact не сохранены)
        if out: rows[x['s']] = (x['t'], x['slot'], x['idx'] or 0, x['signer'], x['fee'], x['cu'] or 0, out)
w = csv.writer(open(sys.argv[2], 'w', newline=''))
w.writerow('ts,slot,txidx,sig,payer,fee,cu,mint,account,delta,dec'.split(','))
for s, (t, sl, ix, pay, fee, cu, out) in sorted(rows.items(), key=lambda kv: (kv[1][1], kv[1][2])):
    for m, a, d, dec in out: w.writerow([t, sl, ix, s, pay, fee, cu, m, a, d, dec])
print(len(rows), 'tx')
