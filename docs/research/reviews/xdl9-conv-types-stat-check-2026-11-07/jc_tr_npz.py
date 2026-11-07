import numpy as np, json, os
p=os.path.expanduser('~/xdl9/c2t/out/ts_tables')
Z=np.load(p+'.npz'); M=json.load(open(p+'.json',encoding='utf-8'))
tr=Z['tr']; cols=M['tr_cols']; ix={c:i for i,c in enumerate(cols)}
rng=np.random.default_rng(37)
def cl_boot(vals, cl, B=2000):
    u,inv=np.unique(cl,return_inverse=True); n=np.bincount(inv).astype(float); s=np.bincount(inv,weights=vals)
    r=[]
    for _ in range(B):
        k=rng.integers(0,len(u),len(u)); r.append(s[k].sum()/n[k].sum())
    return s.sum()/n.sum(), np.percentile(r,5), np.percentile(r,95), len(u)
for un,ui in (('B1',0),('B3',1),('V1',2),('V2',3)):
    for half in (0,1):
        m=(tr[:,ix['uni']]==ui)&(tr[:,ix['half']]==half)&(tr[:,ix['mtm']]==0)
        T=tr[m]; net=T[:,ix['net']]; code=T[:,ix['code']].astype(int); ai=T[:,ix['ai']]
        mu,lo,hi,na=cl_boot(net,ai)
        print('%s %s n=%d assets=%d net %.2f [%.2f %.2f]  code shares:'%(un,'FW'[half],len(T),na,mu,lo,hi),{int(k):round(float((code==k).mean()),3) for k in np.unique(code)})
        K=code==0
        print('     K net %.2f gross %.2f ; nonK net %.2f ; p*=%.4f ; fact stops(non-K) %.4f'%(net[K].mean(),T[K,ix['gross']].mean(),net[~K].mean(), net[K].mean()/(net[K].mean()-net[~K].mean()) if (~K).any() else float('nan'), (~K).mean()))
