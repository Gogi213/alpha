#!/usr/bin/env python3
"""TK-385 y13: сводка csv y13 -> findings csv + печать таблицы; бутстрап по суткам база-MA1 (бп/ч)."""
import csv, glob, sys, os, random
src, out = sys.argv[1], sys.argv[2]
rows = []
for f in sorted(glob.glob(os.path.join(src, '*.csv'))):
    n = os.path.basename(f)[:-4]; coin, sc, ki = n.split('-')
    for r in csv.DictReader(open(f, newline='')):
        r['sc'] = sc; rows.append(r)
cols = ['coin', 'sc', 'k_pct', 'lag_s', 'null_min', 'shift_s', 'hours', 'roundtrips', 'n_limit', 'pnl_limit_rt', 'n_market', 'pnl_market_rt', 'pnl_per_rt', 'pnl_vip0_per_rt', 'pnl_per_h', 'adv60', 'adv300', 'days_pos', 'days_n', 'day_pnl', 'day_n', 'day_h']
with open(out, 'w', newline='') as fo:
    w = csv.writer(fo); w.writerow(cols)
    for r in sorted(rows, key=lambda r: (r['coin'], r['k_pct'], r['sc'])): w.writerow([r.get(c, '') for c in cols])
idx = {(r['coin'], r['sc'], r['k_pct']): r for r in rows}
print('coin k sc | rt/h? rt pnl/rt vip0/rt pnl/h days+/n lim/mkt')
for r in sorted(rows, key=lambda r: (r['coin'], float(r['k_pct']), r['sc'])):
    print(r['coin'], r['k_pct'], r['sc'], '|', r['roundtrips'], r['pnl_per_rt'], r['pnl_vip0_per_rt'], r['pnl_per_h'], f"{r['days_pos']}/{r['days_n']}", f"{r['pnl_limit_rt']}({r['n_limit']})/{r['pnl_market_rt']}({r['n_market']})")
def days(r):
    p = [float(x) for x in r['day_pnl'].split('|')]; h = [float(x) for x in r['day_h'].split('|')]; return p, h
print('\nбутстрап суток: база - нуль, бп/ч (Σpnl/Σh, суточные пары 1..6 полные)')
for (c, sc, k), r in sorted(idx.items()):
    if sc != 'qbase': continue
    for nul in ('qma1', 'qma5', 'qsh3'):
        z = idx.get((c, nul, k))
        if not z: continue
        pb, hb = days(r); pz, hz = days(z)
        d = [(pb[i] - pz[i]) / hb[i] for i in range(1, 7) if hb[i] > 20 and hz[i] > 20]
        if not d: continue
        random.seed(1); bs = sorted(sum(random.choice(d) for _ in d) / len(d) for _ in range(5000))
        print(c, k, nul, f"{sum(d)/len(d):+.0f} [{bs[125]:+.0f}; {bs[4875]:+.0f}] {sum(x>0 for x in d)}/{len(d)}")
