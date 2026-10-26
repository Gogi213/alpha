# TK-409 блок R с очередью: агрегат Rq-*-eps/counts -> клетки X×W×h; избыток = gross - ctrl (бп); кластер монета-сутки; Холм по 12 клеткам X×W на h
# Издержки: вход мейкер Gate 1,3 бп (VIP0 2 бп -35 %), выход рынком 3,25 бп (5 бп -35 %); спред выхода уже в gross (выход по книге).
import pandas as pd, numpy as np, glob, sys
from scipy import stats
d = sys.argv[1]
P = {'AGENCY','ALLINU','CLAUDIA','EMBER','FONE','GT','HOOKED','KIMCHI','LAPTOP','PAID','SI','STAMP','SUE','Q'}
rd = lambda p: pd.concat([pd.read_csv(f) for f in glob.glob(d + p) if len(open(f).read(1))], ignore_index=True)
ep = rd('/Rq-*-eps.csv'); ct = rd('/Rq-*-counts.csv')
FEE = 1.3 + 3.25
def holm(p):
    p = np.array(p); o = np.argsort(p); m = len(p); out = np.empty(m); run = 0
    for i, j in enumerate(o): run = max(run, min(1, (m - i) * p[j])); out[j] = run
    return out
print('монет в счёте', ct.coin.nunique(), '| эпизодов', len(ep), '| с событиями', ct[ct.n_event > 0].coin.nunique())
print('\n== воронка по X,W (сумма по монетам, up+down)')
print(ct.groupby(['X','W'])[['n_event','outlier','drop_age','unfilled','drop_exit_age','exit_thin','ok']].sum().to_string())
ep['ex'] = ep.gross_bp - ep.ctrl_bp
ep['ctrl_bp'] = pd.to_numeric(ep.ctrl_bp, errors='coerce'); ep['ex'] = ep.gross_bp - ep.ctrl_bp  # без контроля -> NaN, в p не идёт
def cell(x):
    if len(x) < 3: return dict(n=len(x), coins=x.coin.nunique())
    # кластер монета-сутки: среднее по кластеру, t-тест между кластерами, односторонний p
    g = x.groupby(['coin','day']).agg(ex=('ex','mean'), gr=('gross_bp','mean'), n=('gross_bp','size')).reset_index()
    ge = g.dropna(subset=['ex'])
    r = dict(n=len(x), coins=x.coin.nunique(), clus=len(g), gross=x.gross_bp.mean(), ctrl=x.ctrl_bp.mean(), exc=x.ex.mean(), exc_med=x.ex.median(),
             net=x.gross_bp.mean() - FEE, win=(x.gross_bp > 0).mean())
    if len(ge) >= 3:
        tt, p = stats.ttest_1samp(ge.ex, 0); r['p1'] = p/2 if ge.ex.mean() > 0 else 1 - p/2
        r['sign_p'] = stats.binomtest(int((ge.ex > 0).sum()), len(ge), 0.5, alternative='greater').pvalue  # робастно к выбросам
    if len(g) >= 3:
        tg, pg = stats.ttest_1samp(g.gr, 0); r['pg1'] = pg/2 if g.gr.mean() > 0 else 1 - pg/2
    return r
for cls, sel in (('G', ~ep.coin.isin(P)), ('P', ep.coin.isin(P)), ('все', ep.coin.notna())):
    for h in (60, 300):
        rows = []
        for (X, W), x in ep[sel & (ep.h == h)].groupby(['X','W']):
            r = cell(x); r.update(X=X, W=W); rows.append(r)
        r = pd.DataFrame(rows)
        if 'p1' in r: r['holm12'] = holm(r.p1.fillna(1).values)
        print(f'\n== Rq класс {cls} h={h}c (избыток над контролем; net = gross − {FEE} бп)'); print(r.round(3).to_string(index=False))
print('\n== по знаку, все, h=60')
for dr in ('up','down'):
    rows = []
    for (X, W), x in ep[(ep.h == 60) & (ep['dir'] == dr)].groupby(['X','W']):
        r = cell(x); r.update(X=X, W=W); rows.append(r)
    print(dr); print(pd.DataFrame(rows).round(3).to_string(index=False))
print('\n== вклад монет (h=60, все X/W): топ по сумме gross')
t = ep[ep.h == 60].groupby('coin').agg(n=('gross_bp','size'), gross=('gross_bp','mean'), s=('gross_bp','sum')).sort_values('s', ascending=False)
print(t.head(10).round(2).to_string()); print('без топ-3 монет:', round(ep[(ep.h == 60) & ~ep.coin.isin(t.index[:3])].gross_bp.mean(), 2))
print('выбросы gross: |gross|>1000бп', int((ep.gross_bp.abs() > 1000).sum()))
