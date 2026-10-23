#!/usr/bin/env python3
"""TK-378: USD-слой для sqd-swaps.csv: WSOL-нога -> $ по SOLUSDT 1м (close минуты свопа), USDC/USDT = $.
Запуск: tk378-sqd-usd.py sqd-swaps.csv solusdt-1m.csv OUT_usd.csv OUT_summary.csv"""
import sys, csv, collections
px = {int(r['ts_min_ms']) // 60000: float(r['close']) for r in csv.DictReader(open(sys.argv[2]))}
r = csv.reader(open(sys.argv[1])); h = next(r)
w = csv.writer(open(sys.argv[3], 'w', newline=''))
w.writerow(h + ['usd', 'price_usd'])
agg = collections.defaultdict(lambda: [0, 0, 0.0]); miss = 0
for t in r:
    q, qty = t[7], float(t[8])
    if q == 'WSOL':
        p = px.get(int(t[0]) // 60)
        if p is None: miss += 1; continue
        usd = qty * p
    else: usd = qty
    m = float(t[6])
    w.writerow(t + ['%.6f' % usd, '%.10g' % (usd / m if m else 0)])
    day = t[0] and __import__('datetime').datetime.fromtimestamp(int(t[0]), __import__('datetime').timezone.utc).strftime('%m-%d')
    a = agg[(t[4], day)]; a[0] += 1; a[1] += 1 if q == 'WSOL' else 0; a[2] += usd
s = csv.writer(open(sys.argv[4], 'w', newline='')); s.writerow(['coin', 'day_utc', 'swaps', 'wsol_swaps', 'usd'])
for k in sorted(agg): s.writerow([k[0], k[1], agg[k][0], agg[k][1], '%.0f' % agg[k][2]])
print('no-price rows', miss)
