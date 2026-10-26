# TK-409 блок R: агрегат curve/counts/events -> числа для итога (Холм по 12 клеток X×W, главная: fut, h=60, up+down вместе)
import pandas as pd, numpy as np, glob, sys
from scipy import stats
d = sys.argv[1]
P = {'AGENCY','ALLINU','CLAUDIA','EMBER','FONE','GT','HOOKED','KIMCHI','LAPTOP','PAID','SI','STAMP','SUE','Q'}
rd = lambda p: pd.concat([pd.read_csv(f) for f in glob.glob(d + p) if len(open(f).read(1))], ignore_index=True)
cv = rd('/R-*-curve.csv'); ct = rd('/R-*-counts.csv')
for c in ('n_ev','n_h','excess_bp','resp_bp','ctrl_bp','dex_move_bp','pre_bp'): cv[c] = pd.to_numeric(cv[c], errors='coerce')
cv = cv[cv.day.astype(str) == 'all']
def holm(p):
    p = np.array(p); o = np.argsort(p); m = len(p); out = np.empty(m); run = 0
    for i, j in enumerate(o): run = max(run, min(1, (m - i) * p[j])); out[j] = run
    return out
print('== число событий (ok) по X,W,знак; fut/spot; монет с событиями')
t = ct.groupby(['mkt','X','W','dir']).agg(n_event=('n_event','sum'), n_ok=('n_ok','sum'), hole=('drop_hole','sum'), age=('drop_age','sum')).reset_index()
t['coins'] = ct[ct.n_event > 0].groupby(['mkt','X','W','dir']).coin.nunique().reindex(pd.MultiIndex.from_frame(t[['mkt','X','W','dir']])).values
print(t.to_string(index=False))
print('\n== монеты с событиями по X (n_event>0, любое W/знак)'); print(ct[ct.n_event > 0].groupby('X').coin.nunique())
def cell(df):
    df = df[df.n_h > 0].dropna(subset=['excess_bp'])
    if len(df) < 2: return dict(coins=len(df), n=int(df.n_h.sum()) if len(df) else 0)
    # по монете: взвешенное по событиям; между монетами — t-тест на средних по монетам
    g = df.groupby('coin').apply(lambda x: pd.Series(dict(ex=np.average(x.excess_bp, weights=x.n_h), rs=np.average(x.resp_bp, weights=x.n_h), n=x.n_h.sum())))
    tt, p = stats.ttest_1samp(g.ex, 0)
    return dict(coins=len(g), n=int(g.n.sum()), resp_w=np.average(g.rs, weights=g.n), exc_w=np.average(g.ex, weights=g.n), exc_coin=g.ex.mean(), exc_med=g.ex.median(), p1=p/2 if g.ex.mean() > 0 else 1 - p/2)
for mkt in ('fut','spot'):
  for cls, sel in (('G', ~cv.coin.isin(P)), ('P', cv.coin.isin(P))):
    for h in (10, 60, 300):
        rows = []
        for (X, W), x in cv[sel & (cv.mkt == mkt) & (cv.h == h)].groupby(['X','W']):
            r = cell(x); r.update(X=X, W=W); rows.append(r)
        r = pd.DataFrame(rows)
        if 'p1' in r: r['holm12'] = holm(r.p1.fillna(1).values)
        print(f'\n== R {mkt} класс {cls} h={h}c (up+down вместе; отклик знаком хода DEX; избыток над контролем, бп)'); print(r.round(3).to_string(index=False))
print('\n== по знаку, fut, G, h=60')
for dr in ('up','down'):
    rows = []
    for (X, W), x in cv[(~cv.coin.isin(P)) & (cv.mkt == 'fut') & (cv.h == 60) & (cv.dir == dr)].groupby(['X','W']):
        r = cell(x); r.update(X=X, W=W); rows.append(r)
    print(dr); print(pd.DataFrame(rows).round(3).to_string(index=False))
ev = rd('/R-*-events.csv'); print('\nсобытий в списке', len(ev))
ev['ap'] = ev.move_pct.abs(); top = ev.sort_values('ap', ascending=False).head(25)
print(top.drop(columns=['ap']).to_string(index=False))
ev.sort_values('ap', ascending=False).drop(columns=['ap']).to_csv(d + '/events-all-sorted.csv', index=False)
