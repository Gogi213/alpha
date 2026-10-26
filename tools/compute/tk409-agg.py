import pandas as pd, numpy as np, glob, sys
from scipy import stats
P={'AGENCY','ALLINU','CLAUDIA','EMBER','FONE','GT','HOOKED','KIMCHI','LAPTOP','PAID','SI','STAMP','SUE','Q'}
def load(p): return pd.concat([pd.read_csv(f) for f in glob.glob(p)],ignore_index=True)
S=load('S-*-cells.csv'); Y=load('Y-*-cells.csv')
print('S coins',S.coin.nunique(),'rows',len(S),S.columns.tolist()[:12]); print(S.tag.unique(), S.A.unique(), S.L.unique(), S.vis.unique())
print('Y coins',Y.coin.nunique(),Y.k.unique(),Y['T'].unique(),Y.vis.unique())
def holm(p):
    p=np.array(p); o=np.argsort(p); m=len(p); out=np.empty(m); run=0
    for i,j in enumerate(o): run=max(run,min(1,(m-i)*p[j])); out[j]=run
    return out
def agg(df,keys,exc='excess',n='n',g='gross'):
    rows=[]
    for k,d in df.groupby(keys):
        d=d[d[n]>0]
        if len(d)<2: rows.append((k,len(d),d[n].sum() if len(d) else 0,np.nan,np.nan,np.nan,1.0)); continue
        w=d[n]; ex=np.average(d[exc],weights=w); gr=np.average(d[g],weights=w)
        # cluster by coin: mean of per-coin excess
        pc=d[exc].values; t,p=stats.ttest_1samp(pc,0); 
        rows.append((k,len(d),int(w.sum()),gr,ex,pc.mean(),p/2 if pc.mean()>0 else 1-p/2))
    r=pd.DataFrame(rows,columns=['cell','coins','n','gross_w','exc_w','exc_coinmean','p1']); r['holm']=holm(r.p1.values) if len(r) else []
    return r
S5=S[(S.L==250)&(S.A==5000)&(S.vis==1000)]
for cls,sel in (('P',S5.coin.isin(P)),('G',~S5.coin.isin(P))):
    d=S5[sel]; r=agg(d,['tag','T','xf','TO']); print('\nS',cls,'coins',d.coin.nunique(),'cells',len(r)); print(r.sort_values('p1').head(6).to_string()); 
    m=d[(d.tag=='grid')&(d['T']==0.02)&(d.xf==0.5)&(d.TO==7200)]; print('MAIN',len(m),m.n.sum(), np.average(m.gross,weights=m.n) if len(m) else None, np.average(m.excess,weights=m.n) if len(m) else None, np.average(m.net_d,weights=m.n) if len(m) else None, m.excess.mean() if len(m) else None)
Y1=Y[Y.vis==1000]
for cls,sel in (('P',Y1.coin.isin(P)),('G',~Y1.coin.isin(P))):
    d=Y1[sel]; r=agg(d,['k','T']); print('\nY',cls,'coins',d.coin.nunique()); print(r.sort_values('p1').to_string())
