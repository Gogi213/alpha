#!/usr/bin/env python3
"""TK-378 v3: эффективный спред Gate-фьюч по сделкам (сторона тейкера = знак size): соседние сделки противоположных сторон
в пределах 1 с -> (p_buy - p_sell)/mid в бп (оба порядка усредняются, дрейф сокращается). Выход: spread.csv (coin,day,n_pairs,mean_bp,med_bp).
Запуск: tk378_gate_spread.py OUT.csv COIN..."""
import sys, os, csv, gzip, statistics
G = '/srv/alpha-sda/tk372-gate/raw/fut'
rows = []
for c in sys.argv[2:]:
    d = os.path.join(G, f'{c}_USDT')
    for fn in sorted(os.listdir(d)):
        tr = []
        for r in csv.DictReader(gzip.open(os.path.join(d, fn), 'rt')):
            t = float(r['create_time_ms'] or r['create_time']); tr.append((t, float(r['size']), float(r['price'])))
        tr.sort(key=lambda x: x[0])
        sp = []
        for a, b in zip(tr, tr[1:]):
            if b[0] - a[0] > 1.0 or a[1] * b[1] >= 0: continue
            pb, ps = (a[2], b[2]) if a[1] > 0 else (b[2], a[2])
            sp.append((pb - ps) / ((pb + ps) / 2) * 1e4)
        day = int(fn[8:10])
        rows.append(dict(coin=c, day=day, n_trades=len(tr), n_pairs=len(sp), mean_bp=round(statistics.mean(sp), 1) if sp else '', med_bp=round(statistics.median(sp), 1) if sp else ''))
        print(rows[-1], flush=True)
w = csv.DictWriter(open(sys.argv[1], 'w', newline=''), list(rows[0])); w.writeheader(); w.writerows(rows)
