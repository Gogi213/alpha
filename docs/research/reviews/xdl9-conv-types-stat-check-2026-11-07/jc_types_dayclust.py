import numpy as np, json, os, sys
sys.path.insert(0, os.path.expanduser('~/xdl9/c2t/ts'))
import ts_types as TY
p=os.path.expanduser('~/xdl9/c2t/out/ts_tables')
Z=np.load(p+'.npz'); M=json.load(open(p+'.json',encoding='utf-8'))
tr=Z['tr']; cols=M['tr_cols']; ix={c:i for i,c in enumerate(cols)}
cuts=json.load(open(os.path.expanduser('~/xdl9/c2t/out/types_cuts_committed.json')))
rng=np.random.default_rng(37)
def clb(v, cl, B=3000):
    u,inv=np.unique(cl,return_inverse=True); n=np.bincount(inv).astype(float); s=np.bincount(inv,weights=v)
    r=[]
    for _ in range(B):
        k=rng.integers(0,len(u),len(u)); r.append(s[k].sum()/n[k].sum())
    return s.sum()/n.sum(), np.percentile(r,5), np.percentile(r,95), len(u)
for un,ui in (('B3',1),('B1',0)):
    m=(tr[:,ix['uni']]==ui)&(tr[:,ix['mtm']]==0)
    T=tr[m]; c={k:T[:,i] for k,i in ix.items()}
    d=TY.derive(c); at=TY.atoms(d,cuts); comps=TY.composites(at,d); fam=TY.family(at,comps)
    half=c['half']; net=c['net']; ai=c['ai']; day=c['day']
    names=['ONE','LQlo','BX&ONE','F3hi&LQlo','LQlo&ONE','M1hi&ONE','F3hi&ONE','DEP2&LQlo','BYL&F3hi','BYS','BYL']
    if un=='B1': names=['AGE1&ONE','ONE']
    for nm in names:
        if nm not in fam: continue
        row=[]
        for h in (0,1):
            mm=fam[nm]&(half==h)
            if mm.sum()<5: row.append('n<5'); continue
            a=clb(net[mm],ai[mm]); dd=clb(net[mm],day[mm])
            row.append('%s n=%d net %.1f asset[%.1f %.1f](%d) day[%.1f %.1f](%d)'%('FW'[h],mm.sum(),a[0],a[1],a[2],a[3],dd[1],dd[2],dd[3]))
        print(un,nm,' | '.join(row))
    # all-trades baseline
    for h in (0,1):
        mm=half==h; a=clb(net[mm],ai[mm]); dd=clb(net[mm],day[mm]); print(un,'ALL','FW'[h],'n=%d net %.2f asset[%.2f %.2f] day[%.2f %.2f]'%(mm.sum(),a[0],a[1],a[2],dd[1],dd[2]))
