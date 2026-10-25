#!/usr/bin/env python3
"""TK-394 свод holes2: по монетам — доля сделок Gate в [бид;аск] внутри валидных окон книги (strict/win), валидная доля суток.
Запуск: tk394-holes2-agg.py HOLES2_DIR OUT.csv"""
import sys, os, json, glob
d, out = sys.argv[1], sys.argv[2]
rows = []
for f in sorted(glob.glob(os.path.join(d, '*.json'))):
    if f.endswith('DONE.json'): continue
    j = json.load(open(f)); D = j['days']
    tr = sum(x.get('trades', 0) for x in D); tv = sum(x.get('trades_valid', 0) for x in D)
    sv = sum(x.get('strict_valid', 0) for x in D); wv = sum(x.get('win_valid', 0) for x in D)
    vs = sum(x['valid_share'] for x in D) / max(len(D), 1)
    rows.append((j['coin'], tr, tv, round(tv / tr, 4) if tr else '', round(sv / tv, 4) if tv else '', round(wv / tv, 4) if tv else '', round(vs, 4)))
with open(out, 'w', newline='') as o:
    o.write('coin,trades,trades_valid,share_valid,strict_in_valid,win_in_valid,book_valid_share\n')
    for r in rows: o.write(','.join(map(str, r)) + '\n')
n = len(rows); p = sum(1 for r in rows if r[5] != '' and r[5] >= 0.95); ps = sum(1 for r in rows if r[4] != '' and r[4] >= 0.95)
print(f'монет {n}; win>=95%: {p}; strict>=95%: {ps}')
