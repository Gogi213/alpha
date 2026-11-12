#!/usr/bin/env python3
"""TK-410 П-31 бой Q1: сводка out/*-curve.csv, *-big.csv по 222 EVM-монетам. Запуск (calc, cwd /data/tk0393/tk410):
python tk410-agg.py OUTDIR RESULTPREFIX. Кривая h=W: класс X (по X_mean строки, бп) x W; среднее exc_exec с весом n, CI — бутстрап по монетам
(кластер), нетто = среднее - 2,6; Холм по (класс x W). Эпизоды >=5 %: доля догона Gate за 1/10/60/300/1800 с от tv, страта 0 (Gate не ушёл)."""
import sys, glob, os, csv
import numpy as np
out, pre = sys.argv[1:3]
XC = [(0, 5), (5, 10), (10, 20), (20, 50), (50, 100), (100, 200), (200, 500), (500, 1e9)]
WS = [1, 5, 10, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 14400, 28800, 43200, 86400]
rows = []
for f in glob.glob(f'{out}/*-curve.csv'):
    for r in csv.DictReader(open(f)):
        if r['h'] != r['W']: continue
        rows.append((r['coin'], int(r['W']), float(r['X_mean']), int(r['n']), float(r['exc_exec']), float(r['exc_mid'])))
coins = sorted({r[0] for r in rows}); ci = {c: i for i, c in enumerate(coins)}
A = np.array([(ci[r[0]], r[1], r[2], r[3], r[4], r[5]) for r in rows])
rng = np.random.default_rng(410)
res = []
for W in WS:
    for lo, hi in XC:
        m = (A[:, 1] == W) & (A[:, 2] >= lo) & (A[:, 2] < hi)
        if m.sum() == 0: continue
        sub = A[m]; n = sub[:, 3].sum()
        if n < 30: res.append(dict(W=W, Xlo=lo, Xhi=hi, n=int(n), coins=len(set(sub[:, 0])))); continue
        mean = (sub[:, 4] * sub[:, 3]).sum() / n; mmid = (sub[:, 5] * sub[:, 3]).sum() / n
        uc = np.array(sorted(set(sub[:, 0]))); bs = []
        grp = {c: sub[sub[:, 0] == c] for c in uc}
        for _ in range(500):
            pk = rng.choice(uc, len(uc)); s = np.concatenate([grp[c] for c in pk])
            bs.append((s[:, 4] * s[:, 3]).sum() / s[:, 3].sum())
        lo_, hi_ = np.percentile(bs, [2.5, 97.5]); se = np.std(bs)
        res.append(dict(W=W, Xlo=lo, Xhi=hi, n=int(n), coins=len(uc), exc_exec=mean, exc_mid=mmid, ci_lo=lo_, ci_hi=hi_, net=mean - 2.6, net_lo=lo_ - 2.6, net_hi=hi_ - 2.6,
                        p=float(2 * (1 - __import__('math').erf(abs(mean) / (se * 2 ** .5 + 1e-12) / 2 ** .5 )) / 2) if se > 0 else 1.0))
tested = [r for r in res if 'p' in r]; N = len(tested)
for k, r in enumerate(sorted(tested, key=lambda r: r['p'])): r['holm_p'] = min(1.0, r['p'] * (N - k))
keys = list(dict.fromkeys(k for r in res for k in r))
with open(pre + '-curve.csv', 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=keys); w.writeheader(); w.writerows(res)
print('tests', N, 'sig_holm<0.05', sum(1 for r in tested if r['holm_p'] < .05), 'net>0 CI', sum(1 for r in tested if r['net_lo'] > 0))
for r in sorted(tested, key=lambda r: -r['net'])[:12]: print({k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()})
# эпизоды
EP = []
for f in glob.glob(f'{out}/*-big.csv'):
    for r in csv.DictReader(open(f)): EP.append(r)
print('episodes', len(EP), 'coins', len({r['coin'] for r in EP}))
def fl(x):
    try: return float(x)
    except ValueError: return float('nan')
ep = []
for lo in (500, 1000, 1500, 2000):
    for st in ('0', '1'):
        S = [r for r in EP if abs(fl(r['X_bp'])) >= lo and r['strat'] == st]
        if not S: continue
        d = dict(Xmin=lo, strat=st, n=len(S), coins=len({r['coin'] for r in S}))
        for o in ('1', '10', '60', '300', '1800'):
            v = np.array([np.sign(fl(r['X_bp'])) * fl(r['G' + o]) * 1e4 / 1e4 / abs(fl(r['X_bp'])) if False else np.sign(fl(r['X_bp'])) * fl(r['G' + o]) / abs(fl(r['X_bp'])) for r in S])
            v = v[np.isfinite(v)]
            d[f'catch_p50_{o}s'] = float(np.median(v)) if len(v) else float('nan'); d[f'n_{o}s'] = len(v)
        ep.append(d)
with open(pre + '-episodes.csv', 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(dict.fromkeys(k for r in ep for k in r))); w.writeheader(); w.writerows(ep)
for d in ep: print({k: (round(v, 3) if isinstance(v, float) else v) for k, v in d.items()})
