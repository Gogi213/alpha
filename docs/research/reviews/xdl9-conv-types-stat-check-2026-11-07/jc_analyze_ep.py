import csv, numpy as np, os, collections
sp=os.path.dirname(os.path.abspath(__file__))
R=[r for r in csv.DictReader(open(os.path.join(sp,'jc_episodes.csv'),encoding='utf-8')) if r['status']=='ok']
print('pairs ok',len(set((r['key'],r['ex1'],r['ex2']) for r in R)))
rng=np.random.default_rng(37)
for th in (10,20,40,80,160):
    Q=[r for r in R if int(r['theta'])==th]
    for variant in ('strict','valid'):
        ep=np.array([float(r['ep_'+variant]) for r in Q]); d1=np.array([float(r['d1_'+variant]) for r in Q]); g3=np.array([float(r['ge3_'+variant]) for r in Q])
        key=np.array([r['key'] for r in Q])
        ua,inv=np.unique(key,return_inverse=True)
        E=np.bincount(inv,weights=ep); D1=np.bincount(inv,weights=d1); G3=np.bincount(inv,weights=g3)
        sh=D1.sum()/E.sum()
        bs=[]
        for _ in range(2000):
            k=rng.integers(0,len(ua),len(ua)); e=E[k].sum()
            if e>0: bs.append(D1[k].sum()/e)
        lo,hi=np.percentile(bs,[5,95])
        has=ep>0
        print('theta %3d %-6s episodes %8d D=1 share %.3f  (asset-cluster 90%% [%.3f %.3f])  D>=3 %6d (%.3f)  pair-mean share %.3f  assets with ep %d, with D>=3 %d'%(th,variant,ep.sum(),sh,lo,hi,g3.sum(),g3.sum()/ep.sum(),np.mean(d1[has]/ep[has]),(E>0).sum(),(G3>0).sum()))
# effect of stale legs: pairs with worst-leg empty >=50%
import math
rows=list(csv.DictReader(open(os.path.join(sp,'jc_episodes.csv'),encoding='utf-8')))
