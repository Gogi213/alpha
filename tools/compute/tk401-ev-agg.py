#!/usr/bin/env python3
"""TK-401 П-29 R4: агрегат боя. Запуск (calc): tk401-ev-agg.py OUTPREFIX evm=EVDIR [sol=EVDIR]. Группы: evm, sol, all (вместе); Холм — семейство R4 = 1344 (ячейки с <3 монет — p=1, в ранжирование не входят), пред-ход Gate (R5), 8 суток отдельно, эфф.N по монето-суткам (R6), охват по рынкам. Главная: excess h=60 с, фьюч, A=5 с; t по монетам (df=монет-1);
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
outp = sys.argv[1]; srcs = [a.split('=', 1) for a in sys.argv[2:]]
FAM = 1344
rows = []; cnt = []; coin_grp = {}
for g, ev in srcs:
    for f in sorted(glob.glob(f'{ev}/*-curve.csv')):
        for r in csv.DictReader(open(f)):
            if r['A'] == '5' and r['excess_bp'] != '' and int(r['h']) <= 900: r['grp'] = g; rows.append(r)
        coin_grp.setdefault(f.split('/')[-1][:-10], g)
    for f in sorted(glob.glob(f'{ev}/*-counts.csv')):
        for r in csv.DictReader(open(f)):
            if r['A'] == '5': r['grp'] = g; cnt.append(r)
def grps(g): return ('evm', 'sol') if g == 'all' else (g,)
GR = [s[0] for s in srcs] + (['all'] if len(srcs) > 1 else [])
cd = {}
for r in cnt:
    for g in ('all', r['grp']): cd.setdefault((g, r['mkt'], r['X'], r['W'], r['dir']), []).append(r)
res = []; days = []
rng = np.random.default_rng(401)
for g in GR:
    cells = {}; dcells = {}
    for r in rows:
        if g != 'all' and r['grp'] != g: continue
        if r['day'] == 'all': cells.setdefault((r['mkt'], r['X'], r['W'], r['dir'], int(r['h'])), []).append(r)
        elif (r['mkt'], r['X'], r['W'], int(r['h'])) == ('fut', '0.005', '60', 60) or (r['mkt'], r['X'], r['W'], int(r['h'])) == ('spot', '0.005', '60', 60): dcells.setdefault((r['mkt'], r['dir'], int(r['day'])), []).append(r)
    gres = []
    for k, rs in cells.items():
        n = np.array([int(r['n_h']) for r in rs], float); ex = np.array([float(r['excess_bp']) for r in rs]); mv = np.array([float(r['dex_move_bp']) for r in rs])
        pre = np.array([float(r['pre_bp']) for r in rs if r['pre_bp'] != ''])
        m = len(rs)
        if m < 3: continue
        t, p = ttest_1samp(ex, 0.0); tp = ttest_1samp(pre, 0.0)[0] if len(pre) >= 3 else np.nan
        sh = (n * ex).sum() / (n * mv).sum() if (n * mv).sum() else np.nan
        bs = []
        for _ in range(1000):
            ix = rng.integers(0, m, m); d = (n[ix] * mv[ix]).sum()
            if d: bs.append((n[ix] * ex[ix]).sum() / d)
        lo, hi = np.percentile(bs, [2.5, 97.5]) if bs else (np.nan, np.nan)
        cc = cd.get((g, k[0], k[1], k[2], k[3]), []); cdays = sum(int(c['coin_days']) for c in cc if c['n_ok'] != '0')
        gres.append(dict(grp=g, mkt=k[0], X=k[1], W=k[2], dir=k[3], h=k[4], coins=m, n=int(n.sum()), coin_days=cdays, mean_ex=ex.mean(), t=t, p=p, mean_pre=pre.mean() if len(pre) else np.nan, t_pre=tp, share=sh, lo=lo, hi=hi))
    order = sorted(range(len(gres)), key=lambda i: gres[i]['p']); mx = 0
    for rank, i in enumerate(order):
        adj = min(1.0, (FAM - rank) * gres[i]['p']); mx = max(mx, adj); gres[i]['holm'] = mx
    res += gres
    for k, rs in sorted(dcells.items()):
        ex = np.array([float(r['excess_bp']) for r in rs]); n = sum(int(r['n_h']) for r in rs)
        t, p = ttest_1samp(ex, 0.0) if len(rs) >= 3 else (np.nan, np.nan)
        days.append(dict(grp=g, mkt=k[0], dir=k[1], day=k[2], coins=len(rs), n=n, mean_ex=ex.mean(), t=t, p=p))
with open(outp + '-agg.csv', 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(res[0].keys())); w.writeheader(); w.writerows(sorted(res, key=lambda r: (r['grp'], r['mkt'], r['X'], r['W'], r['dir'], r['h'])))
with open(outp + '-days.csv', 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(days[0].keys())); w.writeheader(); w.writerows(days)
# охват по рынкам (A=5, X=0,5 %, W=60): монет с рынком / с живыми событиями, свопы, без окна R2, кандидаты, события, дыра, возраст A, живые, монето-сутки
cov = []
for g in GR:
    for mk in ('fut', 'spot'):
        for d in ('up', 'down'):
            cc = [c for c in cd.get((g, mk, '0.005', '60', d), [])]
            if not cc: continue
            S = lambda key: sum(int(c[key]) for c in cc)
            cov.append(dict(grp=g, mkt=mk, dir=d, coins=len(cc), coins_alive=sum(int(c['n_ok']) > 0 for c in cc), swaps=S('n_swaps'), drop_R2_no_window=S('no_window'), cand=S('n_cand'), events=S('n_event'), drop_hole=S('drop_hole'), drop_age_A=S('drop_age'), alive=S('n_ok'), coin_days=S('coin_days')))
with open(outp + '-coverage.csv', 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(cov[0].keys())); w.writeheader(); w.writerows(cov)
for g in GR:
    sub = [r for r in res if r['grp'] == g]
    print(g, 'cells', len(sub), 'семейство', FAM, 'holm<0.05:', sum(r['holm'] < 0.05 for r in sub), 'share lo>0.5:', sum(r['lo'] > 0.5 for r in sub if r['lo'] == r['lo']), 'min holm %.3g' % min(r['holm'] for r in sub))
    for mk in ('fut', 'spot'):
        for d in ('up', 'down'):
            for h in (5, 60, 300):
                for r in sub:
                    if (r['mkt'], r['X'], r['W'], r['dir'], r['h']) == (mk, '0.005', '60', d, h):
                        print(g, mk, d, h, 'coins', r['coins'], 'n', r['n'], 'cd', r['coin_days'], 'ex %.2f t %.2f p %.3g holm %.3g pre %.2f t_pre %.2f share %.3f [%.3f,%.3f]' % (r['mean_ex'], r['t'], r['p'], r['holm'], r['mean_pre'], r['t_pre'], r['share'], r['lo'], r['hi']))
