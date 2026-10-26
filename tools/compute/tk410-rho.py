#!/usr/bin/env python3
"""TK-410 В4: соотношение денег. ρ по монете = оборот24 пула DEX / оборот24 Gate (фьюч, спот) и ликвидность пула / глубина Gate (gev2, медиана);
зависимая: share_gate_med (доля схождений ходом Gate), tconv_med_s (медиана времени схождения) из res2-b4-coins. Снимок оборотов 24 ч (pools-*.csv, list-*.csv).
Запуск: python tk410-rho.py DIR OUT   (DIR с pools-old.csv pools-new.csv list-fut-common.csv list-spot-common.csv res2-b4-coins.csv)"""
import sys, csv, collections
import numpy as np
from scipy.stats import spearmanr
d, out = sys.argv[1:3]
P13 = {'AGENCY','ALLINU','CLAUDIA','EMBER','FONE','GT','HOOKED','KIMCHI','LAPTOP','PAID','SI','STAMP','SUE'}
rd = lambda f: list(csv.DictReader(open(f'{d}/{f}', encoding='utf-8')))
pools = {}
for f in ('pools-old.csv', 'pools-new.csv'):
    for r in rd(f): pools[(r['chain'], r['pool'].lower())] = r
pv = collections.defaultdict(float); pl = collections.defaultdict(float)
for r in pools.values():
    try: pv[r['coin']] += float(r['vol24'] or 0); pl[r['coin']] += float(r['liq'] or 0)
    except ValueError: pass
def gt(f):
    m = {}
    for r in rd(f):
        c = r['gate'].replace('_USDT', '')
        try: m[c] = float(r['turnover24h_usd'])
        except ValueError: pass
    return m
gf, gs = gt('list-fut-common.csv'), gt('list-spot-common.csv')
rows = []
for r in rd('res2-b4-coins.csv'):
    c = r['coin']
    try: dep = float(r['gate_depth_med']); sh = float(r['share_gate_med']); tc = float(r['tconv_med_s']); ep = int(float(r['episodes']))
    except ValueError: continue
    if c not in pv or ep < 30: continue
    rows.append(dict(coin=c, cls='13' if c in P13 else 'rest', ep=ep, share=sh, tconv=tc, pool_vol=pv[c], pool_liq=pl[c], gate_dep=dep,
                     rho_fut=pv[c] / gf[c] if gf.get(c) else np.nan, rho_spot=pv[c] / gs[c] if gs.get(c) else np.nan,
                     rho_liq=pl[c] / dep if dep > 0 else np.nan))
with open(out + '.csv', 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
lines = [f'монет с эпизодами>=30 и пулом: {len(rows)} (13: {sum(r["cls"]=="13" for r in rows)})']
for cls in ('all', '13', 'rest'):
    R = [r for r in rows if cls == 'all' or r['cls'] == cls]
    for feat in ('rho_fut', 'rho_spot', 'rho_liq'):
        for tgt in ('share', 'tconv'):
            x = np.array([r[feat] for r in R]); y = np.array([r[tgt] for r in R]); m = np.isfinite(x) & np.isfinite(y) & (x > 0)
            if m.sum() < 8: lines.append(f'{cls} {feat}~{tgt}: n={m.sum()} мало'); continue
            rs, p = spearmanr(np.log(x[m]), y[m]); lines.append(f'{cls} {feat}~{tgt}: n={m.sum()} spearman={rs:+.2f} p={p:.3g}')
R = rows
for feat in ('rho_fut', 'rho_liq'):
    x = np.array([r[feat] for r in R]); m = np.isfinite(x) & (x > 0); q = np.quantile(x[m], [1/3, 2/3])
    for k, nm in enumerate(('низ', 'сред', 'верх')):
        S = [r for r, xv, mm in zip(R, x, m) if mm and (k == 0 and xv <= q[0] or k == 1 and q[0] < xv <= q[1] or k == 2 and xv > q[1])]
        lines.append(f'терциль {feat} {nm}: n={len(S)} ρ∈[{min(s[feat] for s in S):.3g},{max(s[feat] for s in S):.3g}] share_gate med={np.median([s["share"] for s in S]):.2f} tconv med={np.median([s["tconv"] for s in S]):.0f}c')
open(out + '.txt', 'w', encoding='utf-8').write('\n'.join(lines))
print('\n'.join(lines))
