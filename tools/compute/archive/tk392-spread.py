#!/usr/bin/env python3
"""TK-392 (возврат Судьи): срез по спреду Gate фьюча. Запуск: tk392-spread.py CURVEDIR BBODIR OUT_SPREAD.csv OUT_SLICES.csv
Спред монеты = медиана (ask-bid)/mid в бп по каждой 30-й секунде BBO TK-380; монета с долей секунд bid>ask > 0,1 % (K-048) — вне среза.
Терцили по монетам; ячейка X=0,01 W=60, ключевые h; вес = n событий; CI 95% бутстрап по монетам (1000). Колонки как в tk392-slices.py."""
import csv, glob, gzip, os, sys, collections, numpy as np
cur, bbo, out_sp, out_sl = sys.argv[1:5]
coins = sorted(os.path.basename(f).replace('-curve.csv', '') for f in glob.glob(cur + '/*-curve.csv'))
SP = {}; rows = []
for c in coins:
    fn = f'{bbo}/{c}.csv.gz'
    if not os.path.exists(fn): rows.append((c, '', '', 'нет BBO')); continue
    sp = []; cr = tot = 0
    for i, l in enumerate(gzip.open(fn, 'rt')):
        if i % 30: continue
        p = l.split(',')
        if len(p) < 3: continue
        try: b, a = float(p[1]), float(p[2])
        except ValueError: continue
        tot += 1
        if b > a: cr += 1; continue
        if b > 0: sp.append((a - b) / ((a + b) / 2) * 1e4)
    if not tot: rows.append((c, '', '', 'пусто')); continue
    sh = cr / tot
    if sh > 0.001 or not sp: rows.append((c, round(float(np.median(sp)), 2) if sp else '', round(sh, 4), 'пересечён')); continue
    SP[c] = float(np.median(sp)); rows.append((c, round(SP[c], 2), round(sh, 4), 'ок'))
with open(out_sp, 'w', newline='') as f: w = csv.writer(f); w.writerow('coin spread_med_bp crossed_share status'.split()); w.writerows(rows)
q = np.percentile(list(SP.values()), [33.3, 66.7]); print('coins ok', len(SP), 'terc edges bp', q)
def terc(x): return 'T1' if x < q[0] else ('T2' if x < q[1] else 'T3')
HS = {5, 60, 300, 900, 3600}
D = collections.defaultdict(lambda: collections.defaultdict(list))
for fn in glob.glob(cur + '/*-curve.csv'):
    for r in csv.DictReader(open(fn)):
        c = r['coin']
        if c not in SP or r['day'] != 'all' or float(r['X']) != 0.01 or int(r['W']) != 60 or int(r['h']) not in HS or r['gate_resp_bp'] == '': continue
        D[(terc(SP[c]), r['mkt'], r['dir'], int(r['h']))][c].append((int(r['n']), float(r['dex_move_bp']), float(r['gate_resp_bp']), float(r['ctrl_gate_bp'] or 0)))
rng = np.random.default_rng(3923); res = []
for k in sorted(D):
    a = np.array([[sum(x[0] for x in v), sum(x[0] * x[1] for x in v), sum(x[0] * x[2] for x in v), sum(x[0] * x[3] for x in v)] for v in D[k].values()], float)
    def st(i): s = a[i].sum(0); return s[1] / s[0], s[2] / s[0], s[3] / s[0]
    dm, g, c_ = st(np.arange(len(a))); bs = []
    for _ in range(1000):
        i = rng.integers(0, len(a), len(a)); d2, g2, c2 = st(i); bs.append((g2 - c2) / d2)
    res.append(('спред', k[0], k[1], k[2], k[3], len(a), int(a[:, 0].sum()), round(dm, 1), round(g, 1), round(g - c_, 1), round((g - c_) / dm, 3),
                round(float(np.percentile(bs, 2.5)), 3), round(float(np.percentile(bs, 97.5)), 3)))
with open(out_sl, 'w', newline='') as f:
    w = csv.writer(f); w.writerow('slice value mkt dir h coins n dex_move_bp gate_resp_bp excess_bp share_excess share_lo share_hi'.split()); w.writerows(res)
print(len(res), 'rows')
