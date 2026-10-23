#!/usr/bin/env python3
"""TK-374: net по монетам. usage: tk374-net.py raw.json fees.json out.csv. diff (событие-контроль, бп) по монетам для клеток rev/fwd против fee пула (GeckoTerminal, tk373-fees);
Gate taker не включён (тариф не в репозитории) — net_one_leg = diff - fee_пула, net_round = diff - 2*fee_пула; до Gate-комиссии."""
import sys, json, collections, csv
raw = json.load(open(sys.argv[1])); fees = json.load(open(sys.argv[2]))
CELLS = ['rev|q95|10|60', 'rev|q95|10|600', 'rev|30|60|600', 'rev|100|60|60', 'fwd|30|60|60', 'fwd|30|60|600']
agg = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0.0]))
meta = {}
for r in raw:
    if r['status'] != 'ok': continue
    for k, v in r['cells'].items():
        a = k.split('|'); c = '|'.join(a[:4])
        if c in CELLS: agg[r['coin']][c][0] += v[0]; agg[r['coin']][c][1] += v[4]
    meta[r['coin']] = r['net']
rows = []
for coin, cs in sorted(agg.items()):
    base = coin.split('-')[0]; f = fees.get(f'{base}@{meta[coin]}')
    fee = float(f['fee']) * 100 if f and f.get('fee') not in (None, '') else None
    for c in CELLS:
        n, s = cs.get(c, (0, 0.0))
        if n < 30: continue
        d = s / n
        rows.append([coin, meta[coin], c, n, round(d, 1), '' if fee is None else round(fee, 1), '' if fee is None else round(d - fee, 1), '' if fee is None else round(d - 2 * fee, 1)])
with open(sys.argv[3], 'w', newline='', encoding='utf-8') as fh:
    w = csv.writer(fh); w.writerow(['coin', 'net', 'cell', 'n', 'diff_bp', 'pool_fee_bp', 'net_one_leg_bp', 'net_round_bp']); w.writerows(rows)
print(len(rows), sum(1 for r in rows if r[5] == ''), 'без fee')
for r in rows: print(*r)
