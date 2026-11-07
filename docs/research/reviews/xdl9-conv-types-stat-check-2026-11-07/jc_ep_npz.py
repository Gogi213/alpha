import numpy as np, json, os, sys
p=os.path.expanduser('~/xdl9/c2t/out/ts_tables')
Z=np.load(p+'.npz'); M=json.load(open(p+'.json',encoding='utf-8'))
ep=Z['ep']; cols=M['ep_cols']; ix={c:i for i,c in enumerate(cols)}
print('ep',ep.shape,cols)
rng=np.random.default_rng(37)
def boot_share(mask, cl, B=2000):
    # доля mask по кластерам cl
    u,inv=np.unique(cl,return_inverse=True)
    n=np.bincount(inv).astype(float); k=np.bincount(inv,weights=mask).astype(float)
    r=[]
    for _ in range(B):
        s=rng.integers(0,len(u),len(u)); r.append(k[s].sum()/n[s].sum())
    return k.sum()/n.sum(), np.percentile(r,5), np.percentile(r,95)
edges=[25.2,40,80,160,320,640,1e9]
for half in (0,1):
    m=ep[:,ix['half']]==half
    E=ep[m]
    D=E[:,ix['D']]; ai=E[:,ix['ai']]; day=E[:,ix['day']]
    cl=ai*10000+day
    s,lo,hi=boot_share(D==1,cl)
    print('half',half,'episodes',len(E),'assets',len(np.unique(ai)),'clusters',len(np.unique(cl)),'D==1 share %.4f [%.4f %.4f]'%(s,lo,hi),' D<=2 %.4f D<=5 %.4f D>=1440 %d'%((D<=2).mean(),(D<=5).mean(),(D>=1440).sum()))
    out=E[:,ix['out']]; g=E[:,ix['g_in']]
    print('  outcome codes',{int(k):int((out==k).sum()) for k in np.unique(out)})
    for a,b in zip(edges[:-1],edges[1:]):
        mm=(g>=a)&(g<b)
        if mm.sum()==0: continue
        o=out[mm]
        print('   g_in [%g,%g): n %d  K %.3f  S %.3f  T %.3f  censored(0) %.3f  assets %d  units(asset x day) %d'%(a,b,mm.sum(),(o==1).mean(),(o==2).mean(),(o==3).mean(),(o==0).mean(),len(np.unique(ai[mm])),len(np.unique(cl[mm]))))
