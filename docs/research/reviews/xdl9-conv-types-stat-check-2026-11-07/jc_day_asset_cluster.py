import csv, numpy as np
rng=np.random.default_rng(37)
for V in ('V1','V2'):
    rows=list(csv.DictReader(open('grid_%s/trades.csv'%V)))
    net=np.array([float(r['net']) for r in rows]); code=np.array([r['code'] for r in rows]); asset=np.array([r['key'] for r in rows])
    day=np.array([2465+int(float(r['t_sig']))//1440 for r in rows]); s1=code=='2'
    for nm,cl in (('asset',asset),('signal-day',day),('asset-cluster x day-cluster (two-way, approx via V=Va+Vd-Vad)',None)):
        if cl is None: continue
        u,inv=np.unique(cl,return_inverse=True); n=np.bincount(inv).astype(float); S=np.bincount(inv,weights=net); K=np.bincount(inv,weights=s1.astype(float))
        r=[];ps=[];dd=[]
        for _ in range(4000):
            k=rng.integers(0,len(u),len(u)); r.append(S[k].sum()/n[k].sum()); ps.append(K[k].sum()/n[k].sum())
        print(V,nm,'clusters',len(u),'net 90%% [%.2f %.2f]'%tuple(np.percentile(r,[5,95])),' P(net>0)=%.3f'%(np.array(r)>0).mean(),' S1 share 90%% [%.3f %.3f]'%tuple(np.percentile(ps,[5,95])))
    # two-way: variance estimate Cameron-Gelbach-Miller on mean
    def cvar(cl):
        u,inv=np.unique(cl,return_inverse=True); n=len(net); m=net.mean(); e=net-m
        sc=np.bincount(inv,weights=e); return (sc**2).sum()/n**2
    ka=cvar(asset); kd=cvar(day); kad=cvar(np.array([a+'|'+str(d) for a,d in zip(asset,day)]))
    se2=np.sqrt(max(ka+kd-kad,0)); print(V,'two-way CGM se %.2f (asset %.2f, day %.2f) -> 90%% CI half-width %.2f'%(se2,np.sqrt(ka),np.sqrt(kd),1.645*se2))
