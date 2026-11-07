import csv, numpy as np, sys
rows=list(csv.DictReader(open('grid_V1/trades.csv',encoding='utf-8')))
n=len(rows)
asset=np.array([r['key'] for r in rows]); code=np.array([r['code'] for r in rows]); net=np.array([float(r['net']) for r in rows])
t=np.array([float(r['t_sig']) for r in rows]); s1=code=='2'
# episodes: gap rule on (asset, t_sig) sorted
def nep(gap):
    st=np.flatnonzero(s1); order=st[np.lexsort((t[st],asset[st]))]
    cnt=0; prev=None
    for i in order:
        if prev is None or asset[i]!=asset[prev] or t[i]-t[prev]>gap: cnt+=1
        prev=i
    return cnt
for g in (15,30,60,360,720,1440): print('gap',g,'min ->',nep(g),'episodes among',s1.sum(),'stops (assets',len(set(asset[s1])),')')
# any-asset clustering by time: stops within 30 min regardless of asset
order=np.argsort(t[s1]); ts=t[s1][order]; cl=1+np.sum(np.diff(ts)>30); print('cross-asset time clusters (30 min):',cl)
cl=1+np.sum(np.diff(ts)>120); print('cross-asset time clusters (2h):',cl)
# bootstrap by asset cluster for p, mean net, p*
rng=np.random.default_rng(37)
ua=np.unique(asset); idx={a:np.flatnonzero(asset==a) for a in ua}
B=4000; ps=[]; ms=[]; pst=[]
for _ in range(B):
    pick=rng.choice(len(ua),len(ua))
    ii=np.concatenate([idx[ua[k]] for k in pick])
    ss=s1[ii]; nn=net[ii]
    ps.append(ss.mean()); ms.append(nn.mean())
    mu_n=nn[~ss].mean(); mu_s=nn[ss].mean() if ss.any() else np.nan
    pst.append(mu_n/(mu_n-mu_s))
ps=np.array(ps); ms=np.array(ms); pst=np.array(pst)
print('cluster-boot (assets=%d) p_S1: mean %.4f 90%% [%.4f %.4f]'%(len(ua),ps.mean(),*np.percentile(ps,[5,95])))
print('mean net: 90%% [%.2f %.2f]'%tuple(np.percentile(ms,[5,95])), ' P(net>0)=%.3f'%(ms>0).mean())
print('p*: median %.4f 90%% [%.4f %.4f]'%(np.median(pst),*np.nanpercentile(pst,[5,95])))
print('P(p_S1 > p*) in same resamples', np.mean(ps>pst))
# trade counts by asset: concentration
cnt=np.array([len(idx[a]) for a in ua]); print('trades per asset: max',cnt.max(),'median',np.median(cnt),'n_eff (Kish) assets',cnt.sum()**2/(cnt**2).sum())
