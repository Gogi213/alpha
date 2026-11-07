import numpy as np, json, os
p=os.path.expanduser('~/xdl9/c2t/out/ts_tables')
Z=np.load(p+'.npz'); M=json.load(open(p+'.json',encoding='utf-8'))
ep=Z['ep']; ix={c:i for i,c in enumerate(M['ep_cols'])}
E=ep[ep[:,ix['half']]==1]
base=E[E[:,ix['g_in']]>=50]; L=base[base[:,ix['D']]>60]
pr=[];obs=0
for a in np.unique(L[:,ix['ai']]):
    b=base[base[:,ix['ai']]==a]; pa=b[:,ix['byb']].mean(); la=L[L[:,ix['ai']]==a]; k=len(la)
    has=la[:,ix['byb']].sum()>0
    pany=1-(1-pa)**k
    pr.append((a,k,pa,pany,has))
print('assets with long eps',len(pr),' assets with >=1 on Bybit',sum(x[4] for x in pr))
print('P(all those assets have >=1 Bybit long episode | H0, per-asset base rate) = %.2e'%np.prod([x[3] for x in pr if x[4]]))
print('expected number of assets with >=1 on Bybit = %.1f'%sum(x[3] for x in pr))
# asset-level sign test with Bernoulli(pany) -> P(X>=obs)
dist=np.array([1.0])
for x in pr: dist=np.convolve(dist,[1-x[3],x[3]])
obs=sum(x[4] for x in pr); print('Poisson-binomial P(X>=%d)=%.2e'%(obs,dist[obs:].sum()))
# time clusters: long episodes start times; distinct 'events' (asset, 12h)
ts=np.sort(L[:,ix['t']]); 
print('long episode count',len(L),' distinct (asset,day) ',len({(int(a),int(d)) for a,d in zip(L[:,ix['ai']],L[:,ix['day']])}))
