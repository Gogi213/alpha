import csv, numpy as np, sys, os
sp=os.path.dirname(os.path.abspath(__file__))
rows=list(csv.DictReader(open(os.path.join(sp,'theta_all.csv'),encoding='utf-8')))
for var in ('rel',):
  for th in (10,20,40,80,160):
    R=[r for r in rows if r['var']==var and int(r['theta'])==th and r['status'] if False] if False else [r for r in rows if r['var']==var and int(r['theta'])==th]
    n=np.array([float(r['n_g1']) for r in R]); sg=np.array([float(r['sum_g1']) for r in R])
    nep=np.array([float(r['n_ep']) for r in R]); d1=np.array([float(r['n_d1']) for r in R]); d3=np.array([float(r['n_d3p']) for r in R])
    ntr=np.array([float(r['n']) for r in R])
    key=np.array([r['key'] for r in R])
    tot_n=n.sum(); mean=sg.sum()/tot_n-12.6
    print('theta',th,'pairs',len(R),'episodes',int(nep.sum()),'D=1 share %.3f'%(d1.sum()/nep.sum()),'D>=3',int(d3.sum()),'trades(n_g1)',int(tot_n),'pairs with trades',int((n>0).sum()),'net+1m %.2f'%mean)
    # cluster by contract key (asset): aggregate pair rows with same key
    ua=np.unique(key); idx=[np.flatnonzero(key==a) for a in ua]
    N=np.array([n[i].sum() for i in idx]); S=np.array([sg[i].sum() for i in idx])
    rng=np.random.default_rng(37); ms=[]
    for _ in range(4000):
        k=rng.integers(0,len(ua),len(ua)); 
        if N[k].sum()>0: ms.append(S[k].sum()/N[k].sum()-12.6)
    ms=np.array(ms)
    print('   assets with trades',int((N>0).sum()),'cluster-boot(assets) 90%% [%.1f %.1f] P(<=0)=%.3f'%(*np.percentile(ms,[5,95]),(ms<=0).mean()))
    # one-minute share, by pair-weighted (mean of pair shares among pairs with episodes)
    has=nep>0; print('   pair-mean D=1 share %.3f'%np.mean(d1[has]/nep[has]),' median pair share %.3f'%np.median(d1[has]/nep[has]))
