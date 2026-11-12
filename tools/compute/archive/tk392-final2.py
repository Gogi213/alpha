#!/usr/bin/env python3
"""TK-392 (возврат Судьи D1/D2): Холм без пола бутстрапа и доля догона по excess. Запуск: tk392-final2.py CURVEDIR OUT_HOLM.csv OUT_SHARE.csv
p — кластерный (по монетам) t-тест среднего excess = ход Gate - контроль, вес = число событий монеты; SE линеаризацией
SE^2 = sum(n_i^2 (ex_i - obs)^2) / (sum n)^2 * k/(k-1); t с df = k-1 (k — монет). Холм по семейству mkt x X x W x dir x h{60,300,900}.
Доля по excess = sum(n*excess)/sum(n*dex_move) по монетам; CI — бутстрап по монетам (1000). Эфф. N по монето-суткам не считается:
монеты ходят вместе (общий рынок), CI по монетам — нижняя оценка ширины на >= 15 мин."""
import csv, glob, sys, collections, numpy as np
import math
def tsf(t, df):  # односторонняя хвостовая вероятность t-распределения численным интегрированием (scipy на calc нет)
    t = abs(t)
    if t > 60: return 0.0
    x = np.linspace(t, 60.0, 200001)
    pdf = np.exp(math.lgamma((df + 1) / 2) - math.lgamma(df / 2) - 0.5 * math.log(df * math.pi) - (df + 1) / 2 * np.log1p(x * x / df))
    return float(np.trapz(pdf, x)) if hasattr(np, 'trapz') else float(np.trapezoid(pdf, x))
import functools
@functools.lru_cache(None)
def tppf975(df):  # квантиль 0,975 бисекцией по tsf
    lo, hi = 0.0, 50.0
    for _ in range(40):
        mid = (lo + hi) / 2
        if tsf(mid, df) > 0.025: lo = mid
        else: hi = mid
    return (lo + hi) / 2
A = collections.defaultdict(lambda: collections.defaultdict(list))
for fn in glob.glob(sys.argv[1] + '/*-curve.csv'):
    for r in csv.DictReader(open(fn)):
        if r['day'] != 'all' or r['gate_resp_bp'] == '': continue
        A[(r['mkt'], float(r['X']), int(r['W']), r['dir'])][int(r['h'])].append(
            (r['coin'], int(r['n']), float(r['dex_move_bp']), float(r['gate_resp_bp']), float(r['ctrl_gate_bp'] or 0)))
rng = np.random.default_rng(3922)
H = []; S = []
for k in sorted(A):
    for h in sorted(A[k]):
        v = np.array([x[1:] for x in A[k][h]], float); n = v[:, 0]; ex = v[:, 2] - v[:, 3]; dx = v[:, 1]; kk = len(v)
        obs = (n * ex).sum() / n.sum()
        if h in (60, 300, 900) and kk > 2:
            se = np.sqrt((n ** 2 * (ex - obs) ** 2).sum() / n.sum() ** 2 * kk / (kk - 1))
            t = obs / se if se > 0 else 0.0
            p = 2 * tsf(t, kk - 1); q = tppf975(kk - 1)
            H.append([*k, h, kk, int(n.sum()), round(obs, 2), round(obs - q * se, 2),
                      round(obs + q * se, 2), round(t, 2), p])
        den = (n * dx).sum()
        if den == 0: continue
        sh = (n * ex).sum() / den
        bs = []
        for _ in range(1000):
            i = rng.integers(0, kk, kk); d = (n[i] * dx[i]).sum()
            if d != 0: bs.append((n[i] * ex[i]).sum() / d)
        S.append([*k, h, kk, int(n.sum()), round((n * dx).sum() / n.sum(), 1), round(obs, 2), round(sh, 4),
                  round(float(np.percentile(bs, 2.5)), 4), round(float(np.percentile(bs, 97.5)), 4)])
order = np.argsort([x[-1] for x in H]); m = len(H); prev = 0; adj = {}
for rank, i in enumerate(order):
    prev = max(prev, min(1.0, (m - rank) * H[i][-1])); adj[i] = prev
with open(sys.argv[2], 'w', newline='') as f:
    w = csv.writer(f); w.writerow('mkt X W dir h coins n excess_bp ex_lo ex_hi t p p_holm'.split())
    for i, r in enumerate(H): w.writerow([*r[:-1], f'{r[-1]:.3g}', f'{adj[i]:.3g}'])
with open(sys.argv[3], 'w', newline='') as f:
    w = csv.writer(f); w.writerow('mkt X W dir h coins n dex_move_bp excess_bp share_ex share_ex_lo share_ex_hi'.split()); w.writerows(S)
print(m, 'tests; holm<0.05:', sum(1 for i in adj if adj[i] < 0.05), 'p<0.05 raw:', sum(1 for x in H if x[-1] < .05))
