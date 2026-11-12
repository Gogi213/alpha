#!/usr/bin/env python3
"""TK-398: кластеризация по суткам слоёв отклика Gate на ход DEX. Запуск: tk398-days.py OUT.csv LAYER=CURVEDIR ...
По (слой, рынок, X, W, знак, h): excess = ход Gate - контроль на монето-сутки; три SE: по монетам (как final2), по суткам
(кластер = сутки, df=k-1), по монето-суткам (кластер = пара). Вес = число событий. Сутки 3..7 — колонка day файлов монет."""
import csv, glob, sys, collections, math, numpy as np
def tsf(t, df):
    t = abs(t)
    if t > 60: return 0.0
    x = np.linspace(t, 60.0, 100001)
    pdf = np.exp(math.lgamma((df + 1) / 2) - math.lgamma(df / 2) - 0.5 * math.log(df * math.pi) - (df + 1) / 2 * np.log1p(x * x / df))
    return float((np.trapezoid if hasattr(np, 'trapezoid') else np.trapz)(pdf, x))
def clus(n, ex, g):  # кластерный SE среднего с весами n; g — метки кластеров
    obs = (n * ex).sum() / n.sum(); u = collections.defaultdict(float)
    for ni, ei, gi in zip(n, ex, g): u[gi] += ni * (ei - obs)
    k = len(u); se = math.sqrt(sum(v * v for v in u.values()) / n.sum() ** 2 * k / (k - 1)) if k > 1 else float('nan')
    t = obs / se if se and se > 0 else 0.0
    return obs, se, k, (2 * tsf(t, k - 1) if k > 1 else float('nan'))
out = []
for spec in sys.argv[2:]:
    layer, d = spec.split('=')
    A = collections.defaultdict(list)
    for fn in glob.glob(d + '/*-curve.csv'):
        for r in csv.DictReader(open(fn)):
            if r['day'] == 'all' or r['gate_resp_bp'] == '' or int(r['h']) not in (60, 300, 900): continue
            if float(r['X']) != 0.01 or int(r['W']) != 60: continue
            A[(r['mkt'], r['dir'], int(r['h']))].append((r['coin'], r['day'], int(r['n']), float(r['dex_move_bp']),
                float(r['gate_resp_bp']) - float(r['ctrl_gate_bp'] or 0)))
    for k in sorted(A):
        v = A[k]; n = np.array([x[2] for x in v], float); ex = np.array([x[4] for x in v]); dm = np.array([x[3] for x in v])
        o, sc, kc, pc = clus(n, ex, [x[0] for x in v])
        _, sd, kd, pd_ = clus(n, ex, [x[1] for x in v])
        _, scd, kcd, pcd = clus(n, ex, [(x[0], x[1]) for x in v])
        days = sorted({x[1] for x in v}); dayex = []
        for dd in days:
            m = np.array([x[1] == dd for x in v]); dayex.append((n[m] * ex[m]).sum() / n[m].sum())
        out.append([layer, *k, kc, kd, kcd, int(n.sum()), round(o, 2), round((n * ex).sum() / (n * dm).sum(), 4),
                    round(sc, 2), round(sd, 2), round(scd, 2), f'{pc:.3g}', f'{pd_:.3g}', f'{pcd:.3g}',
                    ' '.join(f'{x:.1f}' for x in dayex), sum(x > 0 for x in dayex)])
w = csv.writer(open(sys.argv[1], 'w', newline=''))
w.writerow('layer mkt dir h coins days coindays n excess_bp share se_coin se_day se_coinday p_coin p_day p_coinday excess_by_day days_pos'.split())
w.writerows(out)
