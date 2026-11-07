import csv, numpy as np, itertools, math
rows=list(csv.DictReader(open('grid_V1/trades.csv',encoding='utf-8')))
asset=np.array([r['key'] for r in rows]); code=np.array([r['code'] for r in rows])
byb=np.array(['bybit' in r['pid'].split('|')[0] for r in rows]); dirv=np.array([float(r['dir']) for r in rows])
pairex=[r['pid'].split('|')[0].split('~') for r in rows]
A_is_byb=np.array([p[0]=='bybit' for p in pairex]); B_is_byb=np.array([p[1]=='bybit' for p in pairex])
# short Bybit: dir=+1 -> short A, long B ; dir=-1 -> long A, short B
shortbyb=((dirv>0)&A_is_byb)|((dirv<0)&B_is_byb)
s1=code=='2'
print('Bybit-leg trade share overall %.3f; stops with bybit %d/%d'%(byb.mean(),byb[s1].sum(),s1.sum()))
# stratified expectation for stops
ua=np.unique(asset[s1]); E=0; V=0; pr=[]
for a in ua:
    m=asset==a; p=byb[m].mean(); k=(s1&m).sum(); E+=k*p; V+=k*p*(1-p); pr+= [p]*k
print('expected Bybit among stops (strat by asset): %.1f +- %.1f'%(E,math.sqrt(V)))
# exact prob all 31 on bybit under independence given per-asset prob
print('P(all %d on Bybit) = %.2e'%(len(pr),np.prod(pr)))
# episode-level: 16 episodes -> take one stop per episode (asset,12h-cluster)
t=np.array([float(r['t_sig']) for r in rows])
st=np.flatnonzero(s1); order=st[np.lexsort((t[st],asset[st]))]
epi=[]; prev=None
for i in order:
    if prev is None or asset[i]!=asset[prev] or t[i]-t[prev]>1440: epi.append([i])
    else: epi[-1].append(i)
    prev=i
print('episodes',len(epi))
# per-episode: probability that episode is on Bybit = share of Bybit among asset's trades (mean) ; all episodes on bybit
p_ep=[byb[asset==asset[e[0]]].mean() for e in epi]
print('episode-level P(all on Bybit) = %.4f'%np.prod(p_ep), ' expected %.1f of %d'%(sum(p_ep),len(epi)))
print('short Bybit among stops',shortbyb[s1].sum(),'of',s1.sum(),' share short-byb among all byb trades: %.3f'%shortbyb[byb].mean())
# shorts vs longs among the asset's trades
E2=0;V2=0
for a in ua:
    m=(asset==a)&byb; p=shortbyb[m].mean() if m.sum() else 0; k=((s1)&(asset==a)).sum(); E2+=k*p; V2+=k*p*(1-p)
print('expected short-Bybit among stops (strat): %.1f +- %.1f'%(E2,math.sqrt(V2)))
# net by Bybit leg / short
net=np.array([float(r['net']) for r in rows])
for nm,m in (('bybit leg',byb),('no bybit',~byb),('short bybit',shortbyb),('long bybit',byb&~shortbyb)):
    print('%-12s n=%3d net %.2f  stops %d'%(nm,m.sum(),net[m].mean(),(s1&m).sum()))
# top-10 asset share of sum |net|, max asset
ab=np.abs(net); ua2=np.unique(asset); sa=np.array([ab[asset==a].sum() for a in ua2]); sa.sort(); print('top10 share of sum|net| %.3f; top1 %.3f'%(sa[-10:].sum()/sa.sum(), sa[-1]/sa.sum()))
# diff p - p* CI via asset-cluster bootstrap
rng=np.random.default_rng(37); idx={a:np.flatnonzero(asset==a) for a in ua2}
d=[]
for _ in range(4000):
    pick=rng.choice(len(ua2),len(ua2)); ii=np.concatenate([idx[ua2[k]] for k in pick]); ss=s1[ii]; nn=net[ii]
    mu_n=nn[~ss].mean(); mu_s=nn[ss].mean(); d.append(ss.mean()-mu_n/(mu_n-mu_s))
print('p - p*: mean %.4f  90%% [%.4f %.4f]'%(np.mean(d),*np.percentile(d,[5,95])))
