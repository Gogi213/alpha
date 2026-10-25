#!/usr/bin/env python3
"""TK-401 П-29 R4: агрегат боя. Запуск (calc): tk401-ev-agg.py EVDIR OUTPREFIX. Главная: excess h=60 с, фьюч, A=5 с; t по монетам (df=монет-1);
семейство Холма X4×W3×знак2×h(≤900 с)×рынок2 при A=5 с (монеты не входят). Доля догона = Σn·excess/Σn·ход, CI бутстрап по монетам 1000 (сид 401)."""
import sys, glob, csv, math
import numpy as np

def _betacf(a, b, x):
    qab = a + b; qap = a + 1; qam = a - 1; c = 1.0; d = 1 - qab * x / qap; d = 1 / (d if abs(d) > 1e-300 else 1e-300); h = d
    for m in range(1, 300):
        m2 = 2 * m; aa = m * (b - m) * x / ((qam + m2) * (a + m2)); d = 1 + aa * d; d = 1 / (d if abs(d) > 1e-300 else 1e-300); c = 1 + aa / c; c = c if abs(c) > 1e-300 else 1e-300; h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2)); d = 1 + aa * d; d = 1 / (d if abs(d) > 1e-300 else 1e-300); c = 1 + aa / c; c = c if abs(c) > 1e-300 else 1e-300; de = d * c; h *= de
        if abs(de - 1) < 1e-12: break
    return h
def ttest_1samp(x, mu):
    n = len(x); sd = np.std(x, ddof=1)
    if sd == 0: return 0.0, 1.0
    t = (np.mean(x) - mu) / (sd / math.sqrt(n)); v = n - 1; x_ = v / (v + t * t)
    lb = math.lgamma((v + 1) / 2) - math.lgamma(v / 2) - 0.5 * math.log(math.pi * v)
    a, b = v / 2, 0.5; bt = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x_) + b * math.log(1 - x_))
    ib = bt * _betacf(a, b, x_) / a if x_ < (a + 1) / (a + b + 2) else 1 - bt * _betacf(b, a, 1 - x_) / b
    return t, ib
ev, outp = sys.argv[1:3]
rows = []
for f in sorted(glob.glob(f'{ev}/*-curve.csv')):
    for r in csv.DictReader(open(f)):
        if r['day'] == 'all' and r['A'] == '5' and r['excess_bp'] != '' and int(r['h']) <= 900: rows.append(r)
cells = {}
for r in rows: cells.setdefault((r['mkt'], r['X'], r['W'], r['dir'], int(r['h'])), []).append(r)
rng = np.random.default_rng(401); res = []
for k, rs in cells.items():
    n = np.array([int(r['n_h']) for r in rs], float); ex = np.array([float(r['excess_bp']) for r in rs]); mv = np.array([float(r['dex_move_bp']) for r in rs])
    m = len(rs)
    if m < 3: continue
    t, p = ttest_1samp(ex, 0.0)
    sh = (n * ex).sum() / (n * mv).sum() if (n * mv).sum() else np.nan
    bs = []
    for _ in range(1000):
        ix = rng.integers(0, m, m); d = (n[ix] * mv[ix]).sum()
        if d: bs.append((n[ix] * ex[ix]).sum() / d)
    lo, hi = np.percentile(bs, [2.5, 97.5]) if bs else (np.nan, np.nan)
    res.append(dict(mkt=k[0], X=k[1], W=k[2], dir=k[3], h=k[4], coins=m, n=int(n.sum()), mean_ex=ex.mean(), t=t, p=p, share=sh, lo=lo, hi=hi))
# Холм
order = sorted(range(len(res)), key=lambda i: res[i]['p']); M = len(res); mx = 0
for rank, i in enumerate(order):
    adj = min(1.0, (M - rank) * res[i]['p']); mx = max(mx, adj); res[i]['holm'] = mx
with open(outp + '-agg.csv', 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(res[0].keys())); w.writeheader(); w.writerows(sorted(res, key=lambda r: (r['mkt'], r['X'], r['W'], r['dir'], r['h'])))
print('cells', M, 'holm<0.05:', sum(r['holm'] < 0.05 for r in res), 'share lo>0.5:', sum(r['lo'] > 0.5 for r in res if r['lo'] == r['lo']))
for mk in ('fut', 'spot'):
    for X, W in (('0.005', '60'),):
        for d in ('up', 'down'):
            for h in (5, 30, 60, 300, 900):
                for r in res:
                    if (r['mkt'], r['X'], r['W'], r['dir'], r['h']) == (mk, X, W, d, h):
                        print(mk, X, W, d, h, 'coins', r['coins'], 'n', r['n'], 'ex %.2f t %.2f p %.3g holm %.3g share %.3f [%.3f,%.3f]' % (r['mean_ex'], r['t'], r['p'], r['holm'], r['share'], r['lo'], r['hi']))
