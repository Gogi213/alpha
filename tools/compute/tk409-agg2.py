"""TK-409 агрегат v2 (возврат Судьи 26.10 06:00): Холм по семейству (S-клеток + Y-клеток) на класс, P по монетам + кластерный t (df=число монет-1),
чувствительность L/A главной клетки, годные сутки. Запуск: python tk409-agg2.py <каталог пула> (S-*-cells.csv, Y-*-cells.csv, S-*-counts.csv)."""
import pandas as pd, numpy as np, glob, sys, os
from scipy import stats
D = sys.argv[1]
P = {'AGENCY','ALLINU','CLAUDIA','EMBER','FONE','GT','HOOKED','KIMCHI','LAPTOP','PAID','SI','STAMP','SUE','Q'}
def load(p): return pd.concat([pd.read_csv(f) for f in glob.glob(os.path.join(D, p))], ignore_index=True)
S = load('S-*-cells.csv'); S = S[S.tag == 'grid'].copy() if False else S; Y = load('Y-*-cells.csv'); C = load('S-*-counts.csv')
SK = ['tag','T','xf','TO']; YK = ['k','T']
nS = S[S.tag == 'grid'].groupby(SK).ngroups; nY = Y.groupby(YK).ngroups; M = 114  # V5: S 3 формы×4 T×2 xf×4 TO = 96 + Y 3×2×3 = 18; считаны только клетки формы (а) и часть Y — остальное p=1 (консервативно)
print(f'семейство {M}: клеток с данными S {nS} + Y {nY}; остальные p=1')
def holm_fixed(p, m):
    p = np.array(p); o = np.argsort(p); out = np.empty(len(p)); run = 0
    for i, j in enumerate(o): run = max(run, min(1, (m - i) * p[j])); out[j] = run
    return out
def cellstat(d, keys):
    rows = []
    for k, g in d[d.n > 0].groupby(keys):
        pc = g.excess.values
        if len(pc) < 2: p = 1.0; t = np.nan
        else:
            t, p2 = stats.ttest_1samp(pc, 0); p = p2 / 2 if pc.mean() > 0 else 1 - p2 / 2
        rows.append((('S' if 'tag' in keys else 'Y',) + (k if isinstance(k, tuple) else (k,)), len(g), int(g.n.sum()), np.average(g.gross, weights=g.n), np.average(g.excess, weights=g.n), pc.mean(), p))
    return pd.DataFrame(rows, columns=['cell','coins','n','gross_w','exc_w','exc_coinmean','p1'])
S5 = S[(S.L == 250) & (S.A == 5000) & (S.vis == 1000) & (S.tag == 'grid')]; Y1 = Y[Y.vis == 1000]
for cls, isP in (('P', True), ('G', False)):
    s = S5[S5.coin.isin(P) == isP]; y = Y1[Y1.coin.isin(P) == isP]
    r = pd.concat([cellstat(s, SK), cellstat(y, YK)], ignore_index=True)
    allp = np.concatenate([r.p1.values, np.ones(max(0, M - len(r)))])  # пустые клетки p=1
    r['holm'] = holm_fixed(allp, M)[:len(r)]
    print(f'\n== класс {cls}: монет S {s.coin.nunique()} Y {y.coin.nunique()}, клеток с данными {len(r)} из {M}; Холм по {M}')
    print(r.sort_values('p1').head(5).to_string())
    print(f'min Холм {r.holm.min():.3f}')
    m = s[(s.tag == 'grid') & (s['T'] == 0.02) & (s.xf == 0.5) & (s.TO == 7200)]
    if len(m): print(f'S главная: монет {len(m)}, n {int(m.n.sum())}, валовый {np.average(m.gross, weights=m.n):.2f}, избыток {np.average(m.excess, weights=m.n):.2f}, нетто_d {np.average(m.net_d, weights=m.n):.2f}, по монетам {m.excess.mean():.2f}')
    if cls == 'P':
        print('P по монетам (S главная):'); print(m[['coin','n','gross','excess','ci_lo','ci_hi','net_d','n_days','good_days']].to_string(index=False))
        if len(m) > 1:
            t, p2 = stats.ttest_1samp(m.excess.values, 0); print(f'кластерный t по монетам df={len(m)-1}: t {t:.2f} p(2ст.) {p2:.3f}')
        yy = y[(y.k == y.k.unique()[0])]
# чувствительность L/A (главная клетка), оба класса
print('\n== чувствительность главной клетки S (избыток, n; веса n)')
for cls, isP in (('P', True), ('G', False)):
    for (L, A), g in S[(S.coin.isin(P) == isP) & (S.vis == 1000) & (S['T'] == 0.02) & (S.xf == 0.5) & (S.TO == 7200)].drop_duplicates(['coin','L','A']).groupby(['L','A']):
        if g.n.sum(): print(f'{cls} L={L} A={A}: монет {len(g)} n {int(g.n.sum())} валовый {np.average(g.gross, weights=g.n):.2f} избыток {np.average(g.excess, weights=g.n):.2f}')
# V7 годные сутки
cg = C.drop_duplicates('coin')[['coin','good_days']]
print('\n== V7 годные сутки: монет', len(cg), 'медиана', cg.good_days.median(), 'распределение', cg.good_days.value_counts().sort_index().to_dict())
print('P:', cg[cg.coin.isin(P)].set_index('coin').good_days.to_dict())
