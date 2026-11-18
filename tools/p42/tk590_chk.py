# TK-590 critic small checks on U1/U0 episodes (days 14..73 only; holdout not in table)
import numpy as np, pandas as pd, json
from scipy.optimize import minimize
from scipy.stats import rankdata
rng=np.random.default_rng(590)
E=pd.read_parquet('/data/p42-t588/episodes.parquet')
E['y']=(E.o_x1_t60==3).astype(int); E['yf']=(E.o_x1_t60_f01==3).astype(int)
E['lturn24']=np.log10(E.turn24h.clip(lower=1)); E['lturn1']=np.log10(E.turn1h.clip(lower=1))
E['zone']=(E.pierce_ep>=1).astype(int)
def auc(x,y):
    x=np.asarray(x,float); y=np.asarray(y); m=~np.isnan(x); x=x[m]; y=y[m]
    n1=y.sum(); n0=len(y)-n1
    if n1==0 or n0==0: return np.nan
    r=rankdata(x); return (r[y==1].sum()-n1*(n1+1)/2)/(n1*n0)
out=[]
def P(*a): s=' '.join(str(x) for x in a); print(s); out.append(s)
U1=E[E.univ=='U1']
P('== label base rates U1', U1.groupby('side')[['y','yf']].agg(['sum','mean']).round(3).to_dict())
# A. Spearman between features (U1, per side)
F=['lturn24','lturn1','tpm60','age_h','natr_bp','pierce_ep','spread_tau','cons60','mv5','c5_btc_7d','since_touch_h','flat60','depth_min_bp']
for sd,g in U1.groupby('side'):
    C=g[F].rank().corr()
    P('== spearman',sd); P(C.loc[['lturn24','age_h','natr_bp'],F].round(2).to_string())
# B. tertiles of turnover: zone vs clean, tpm60 AUC, cons60, age_h AUC within strata
for sd,g in U1.groupby('side'):
    q=g.lturn24.quantile([1/3,2/3]).values; t=np.digitize(g.lturn24,q)
    for lab in ['y','yf']:
        P(f'== {sd} {lab} by turn24 tertile: n, rate, zone rate/clean rate, AUC tpm60, AUC age_h, AUC cons60, AUC pierce_ep, AUC natr, cons60=0 rate / >0 rate')
        for k in range(3):
            h=g[t==k]; y=h[lab].values
            P(k,len(h),round(y.mean()*100,1),
              f"{h[h.zone==1][lab].mean()*100:.1f}/{h[h.zone==0][lab].mean()*100:.1f} (n {int(h.zone.sum())}/{int((1-h.zone).sum())})",
              round(auc(h.tpm60,y),3),round(auc(h.age_h,y),3),round(auc(h.cons60,y),3),round(auc(h.pierce_ep,y),3),round(auc(h.natr_bp,y),3),
              f"{h[h.cons60==0][lab].mean()*100:.1f}/{h[h.cons60>0][lab].mean()*100:.1f}")
# C. Simpson check: pooled vs within-half for zone; per-coin concentration
for sd,g in U1.groupby('side'):
    pc=g.groupby('sym').y.sum().sort_values(ascending=False)
    P('== positives per coin',sd,'coins with >=1:',int((pc>0).sum()),'top5:',pc.head(5).to_dict(),'top5 share',round(pc.head(5).sum()/pc.sum(),2))
    pcf=g.groupby('sym').yf.sum().sort_values(ascending=False)
    P('   yf coins >=1:',int((pcf>0).sum()),'top5 share',round(pcf.head(5).sum()/pcf.sum(),2))
    # within-coin vs between-coin turnover effect: demean lturn24 by coin
    g=g.copy(); g['lt_w']=g.lturn24-g.groupby('sym').lturn24.transform('mean'); g['lt_b']=g.groupby('sym').lturn24.transform('mean')
    P('   AUC lturn24 total/between-coin mean/within-coin dev:',round(auc(g.lturn24,g.y),3),round(auc(g.lt_b,g.y),3),round(auc(g.lt_w,g.y),3),
      '| yf:',round(auc(g.lturn24,g.yf),3),round(auc(g.lt_b,g.yf),3),round(auc(g.lt_w,g.yf),3))
# D. penalized logistic, time split A<->B, OOS AUC with coin bootstrap of delta vs baseline
def prep(tr,te,cols):
    Xtr=tr[cols].astype(float).copy(); Xte=te[cols].astype(float).copy()
    for c in cols:
        med=Xtr[c].median(); Xtr[c]=Xtr[c].fillna(med); Xte[c]=Xte[c].fillna(med)
        lo,hi=Xtr[c].quantile([.01,.99]); Xtr[c]=Xtr[c].clip(lo,hi); Xte[c]=Xte[c].clip(lo,hi)
        mu,sdv=Xtr[c].mean(),Xtr[c].std() or 1.0; Xtr[c]=(Xtr[c]-mu)/sdv; Xte[c]=(Xte[c]-mu)/sdv
    return Xtr.values,Xte.values
def fit(X,y,lam):
    X1=np.c_[np.ones(len(X)),X]
    def f(w):
        z=X1@w; l=np.logaddexp(0,z)-y*z; p=1/(1+np.exp(-z))
        return l.sum()+lam*(w[1:]**2).sum()/2, X1.T@(p-y)+lam*np.r_[0,w[1:]]
    return minimize(f,np.zeros(X1.shape[1]),jac=True,method='L-BFGS-B').x
def pred(w,X): return np.c_[np.ones(len(X)),X]@w
PRIM=['pierce_ep','spread_tau','tpm60','mv5','cons60','c5_btc_7d','c5_eth_7d','flat60']
EXPL=PRIM+['lturn24','lturn1','age_h','since_born_h','natr_bp','depth_min_bp','depth_mean_bp','n_piv','ext_gap_bp','since_touch_h','since_touch_prior_h','approach_dur_min','touch_ep','pierce_max_bp','dist_bp','tpm15','gap60_s','mv15','mv60','maxmv1','maxrng15','cons120','c5_btc_24h','c15_btc_7d','btc_mv5','btc_mv15','eth_mv5','eth_mv15','cosig15','peers7','peer_sig30']
MODELS={'turn24 only':['lturn24'],'turn24+age+natr':['lturn24','age_h','natr_bp'],'8 primary':PRIM,'8 primary + turn24':PRIM+['lturn24'],'all 39':EXPL}
def oos(D,lab,name):
    for sd,g in D.groupby('side'):
        res={}
        for tr_h,te_h in [('A','B'),('B','A')]:
            tr=g[g.half==tr_h]; te=g[g.half==te_h]
            # embargo: drop test episodes within 1 day of split boundary (label T=60 min << 1 day)
            sc={}
            for m,cols in MODELS.items():
                Xtr,Xte=prep(tr,te,cols); lam=max(1.0, len(cols)*2.0)
                w=fit(Xtr,tr[lab].values.astype(float),lam); sc[m]=pred(w,Xte)
            yte=te[lab].values; coins=te.sym.values; uc=np.unique(coins); idx={c:np.where(coins==c)[0] for c in uc}
            row={}
            for m in MODELS:
                a=auc(sc[m],yte); bs=[]
                for _ in range(300):
                    ii=np.concatenate([idx[c] for c in rng.choice(uc,len(uc))])
                    bs.append(auc(sc[m][ii],yte[ii])-auc(sc['turn24 only'][ii],yte[ii]))
                bs=np.array(bs); bs=bs[~np.isnan(bs)]
                row[m]=f"{a:.3f} (Δ vs turn24 {a-auc(sc['turn24 only'],yte):+.3f} [{np.quantile(bs,.025):+.3f};{np.quantile(bs,.975):+.3f}])"
            res[f'{tr_h}->{te_h} npos_tr={int(tr[lab].sum())} npos_te={int(yte.sum())}']=row
        P(f'== OOS logistic {name} {lab} {sd}'); [P(' ',k,json.dumps(v,ensure_ascii=False)) for k,v in res.items()]
for lab in ['y','yf']: oos(U1,lab,'U1')
# E. U0 (pool off) same, indep only to limit size, label y and yf
U0=E[(E.univ=='U0')&(E.indep==1)]
P('== U0 indep n',U0.groupby('side').size().to_dict(),'pos y',U0.groupby('side').y.sum().to_dict(),'pos yf',U0.groupby('side').yf.sum().to_dict())
U0all=E[E.univ=='U0']; P('== U0 all pos y',U0all.groupby('side').y.sum().to_dict(),'yf',U0all.groupby('side').yf.sum().to_dict())
for lab in ['y','yf']: oos(U0,lab,'U0indep')
open('/data/tk590/chk.out','w').write('\n'.join(out))
