#!/usr/bin/env python3
"""TK-410 П-31 бой Q1, доводка: В2 (conv), В3 (conv.first, xc), В4 (feat x conv), В5 (путь до/после, t50/t90) по out2/*.
Запуск (calc, cwd /data/tk0393/tk410): python tk410-agg2.py OUTDIR PREFIX. Кривая В1 и эпизоды — tk410-agg.py."""
import sys, glob, csv
import numpy as np
out, pre = sys.argv[1:3]
rng = np.random.default_rng(411)
def rd(k):
    R = []
    for f in glob.glob(f'{out}/*-{k}.csv'): R += list(csv.DictReader(open(f)))
    return R
def fl(x):
    try: return float(x)
    except (ValueError, TypeError): return float('nan')
def boot_med(vals, coins, f=np.nanmedian):
    uc = sorted(set(coins)); co = np.array(coins); v = np.array(vals); g = {c: v[co == c] for c in uc}; b = []
    for _ in range(300):
        pk = rng.choice(len(uc), len(uc)); b.append(f(np.concatenate([g[uc[i]] for i in pk])))
    return np.percentile(b, [2.5, 97.5])
res = []
# ---- В2/В3: conv
C = rd('conv'); print('conv episodes', len(C), 'coins', len({r['coin'] for r in C}))
BINS = [(30, 50), (50, 100), (100, 200), (200, 1e9)]
for lo, hi in BINS:
    S = [r for r in C if lo <= abs(fl(r['d0_bp'])) < hi]
    if not S: continue
    t = np.array([fl(r['tconv_s']) for r in S]); obs = np.array([fl(r['obs_s']) for r in S]); coins = [r['coin'] for r in S]
    d = dict(block='B2', d0_lo=lo, d0_hi=hi, n=len(S), coins=len(set(coins)), conv_share=float(np.isfinite(t).mean()))
    for T in (1, 10, 60, 300, 1800, 3600, 86400): d[f'conv_by_{T}s'] = float((t <= T).mean())
    d['tconv_med_s'] = float(np.nanmedian(t)) if np.isfinite(t).any() else float('nan')
    if np.isfinite(t).sum() > 10:
        l, h = boot_med(t, coins); d['tconv_med_lo'], d['tconv_med_hi'] = float(l), float(h)
    d['obs_full_share'] = float((obs >= 86400 - 1).mean())
    fs = [r['first'] for r in S]
    for k in ('gate', 'pool', 'same', 'none'): d[f'first_{k}'] = float(np.mean([x == k for x in fs]))
    sgt = np.array([fl(r['share_gate']) for r in S]); sgt = sgt[np.isfinite(sgt)]
    d['share_gate_med'] = float(np.median(sgt)) if len(sgt) else float('nan'); d['share_gate_n'] = len(sgt)
    d['conv_src_gate'] = float(np.mean([r['conv_src'] == 'gate' for r in S if r['conv_src']])) if any(r['conv_src'] for r in S) else float('nan')
    res.append(d)
# ---- В3: xc, среднее по монетам (nzD>=30), бутстрап по монетам
X = [r for r in rd('xc') if fl(r['corr']) == fl(r['corr']) and int(r['nzD']) >= 30]
xr = []
for w in (10, 60, 300):
    for tau in (-300, -60, -10, 0, 10, 60, 300):
        S = [r for r in X if int(r['w']) == w and int(r['tau']) == tau]
        if len(S) < 5: continue
        v = np.array([fl(r['corr']) for r in S]); co = [r['coin'] for r in S]
        l, h = boot_med(v, co, np.nanmean)
        xr.append(dict(block='B3xc', w=w, tau=tau, coins=len(S), corr_mean=float(v.mean()), lo=float(l), hi=float(h)))
res += xr
for r in xr: print('xc', r['w'], r['tau'], r['coins'], round(r['corr_mean'], 4), round(r['lo'], 4), round(r['hi'], 4))
# ---- В4: признаки на монету -> медиана доли Gate и времени схождения
F = {r['coin']: r for r in rd('feat')}
per = {}
for r in C:
    per.setdefault(r['coin'], []).append(r)
rows4 = []
for c, rs in per.items():
    if c not in F or len(rs) < 5: continue
    sg = np.array([fl(r['share_gate']) for r in rs]); t = np.array([fl(r['tconv_s']) for r in rs])
    rows4.append((c, fl(F[c]['gate_depth_med']), fl(F[c]['pool_events']), fl(F[c]['gate_events']), np.nanmedian(sg) if np.isfinite(sg).any() else np.nan, np.nanmedian(t) if np.isfinite(t).any() else np.nan, len(rs)))
def spear(a, b):
    m = np.isfinite(a) & np.isfinite(b)
    if m.sum() < 10: return float('nan'), int(m.sum())
    ra = np.argsort(np.argsort(a[m])); rb = np.argsort(np.argsort(b[m])); return float(np.corrcoef(ra, rb)[0, 1]), int(m.sum())
A = np.array([r[1:] for r in rows4], float)
for nm, i in (('gate_depth', 0), ('pool_events', 1), ('gate_events', 2)):
    for tg, j in (('share_gate', 3), ('tconv', 4)):
        s, n = spear(A[:, i], A[:, j]); res.append(dict(block='B4', feat=nm, target=tg, spearman=s, coins=n)); print('B4', nm, tg, round(s, 3), n)
with open(pre + '-b4-coins.csv', 'w', newline='') as f:
    w = csv.writer(f); w.writerow(['coin', 'gate_depth_med', 'pool_events', 'gate_events', 'share_gate_med', 'tconv_med_s', 'episodes']); w.writerows(rows4)
# ---- В5: big
B = rd('big'); print('big', len(B), 'coins', len({r['coin'] for r in B}))
for lo in (500, 1000, 1500, 2000):
    for st in ('0', '1', '2'):
        S = [r for r in B if abs(fl(r['X_bp'])) >= lo and r['strat'] == st]
        if not S: continue
        d = dict(block='B5', Xmin=lo, strat=st, n=len(S), coins=len({r['coin'] for r in S}))
        t50 = np.array([fl(r['t50']) for r in S]); t90 = np.array([fl(r['t90']) for r in S]); lo_ = np.array([fl(r['last_obs']) for r in S])
        d['reach50'] = float(np.isfinite(t50).mean()); d['reach90'] = float(np.isfinite(t90).mean())
        d['t50_med_s'] = float(np.nanmedian(t50)) if np.isfinite(t50).any() else float('nan'); d['t90_med_s'] = float(np.nanmedian(t90)) if np.isfinite(t90).any() else float('nan')
        d['last_obs_med_s'] = float(np.median(lo_))
        pre_v = []
        for r in S:
            x = fl(r['X_bp']); W = int(r['W']); v = fl(r.get(f'G{-W}', ''))
            if v == v and x == x: pre_v.append(np.sign(x) * v / abs(x * 1e-4))   # G* — лог-разность Gate? см. примечание в отчёте
        d['pre_share_med'] = float(np.median(pre_v)) if pre_v else float('nan'); d['pre_n'] = len(pre_v)
        for o in (1, 10, 60, 300, 1800, 3600, 14400, 86400):
            v = np.array([np.sign(fl(r['X_bp'])) * fl(r.get(f'G{o}', '')) / abs(fl(r['X_bp']) * 1e-4) for r in S]); v = v[np.isfinite(v)]
            d[f'post_med_{o}s'] = float(np.median(v)) if len(v) else float('nan'); d[f'post_n_{o}s'] = len(v)
        res.append(d); print({k: (round(v, 3) if isinstance(v, float) else v) for k, v in d.items() if k in ('Xmin', 'strat', 'n', 'coins', 'reach50', 'reach90', 't50_med_s', 't90_med_s', 'pre_share_med', 'pre_n', 'post_med_60s', 'post_med_1800s', 'post_med_86400s')})
keys = list(dict.fromkeys(k for r in res for k in r))
with open(pre + '-b2b3b4b5.csv', 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=keys); w.writeheader(); w.writerows(res)
for r in res:
    if r['block'] == 'B2': print({k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()})
