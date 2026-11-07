import numpy as np, json, os
p=os.path.expanduser('~/xdl9/c2t/out/ts_tables')
Z=np.load(p+'.npz'); M=json.load(open(p+'.json',encoding='utf-8'))
ep=Z['ep']; ix={c:i for i,c in enumerate(M['ep_cols'])}
rng=np.random.default_rng(37)
def clb(v, cl, B=3000):
    u,inv=np.unique(cl,return_inverse=True); n=np.bincount(inv).astype(float); s=np.bincount(inv,weights=v)
    r=[]
    for _ in range(B):
        k=rng.integers(0,len(u),len(u)); r.append(s[k].sum()/n[k].sum())
    return s.sum()/n.sum(), np.percentile(r,5), np.percentile(r,95), len(u)
for h in (0,1):
    E=ep[ep[:,ix['half']]==h]; v=(E[:,ix['D']]==1).astype(float)
    for nm,cl in (('day',E[:,ix['day']]),('asset',E[:,ix['ai']]),('day x hour-block',E[:,ix['day']]*100+E[:,ix['t']]%1440//240)):
        r=clb(v,cl); print('FW'[h],nm,'clusters',r[3],'D=1 share %.4f [%.4f %.4f]'%r[:3])
    # by min-D>1 excluding hair: share of D<=2
