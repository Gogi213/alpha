import numpy as np, pandas as pd, json
exec(open('/data/tk590/chk.py').read().split('# D. penalized')[0].split("out=[]")[0])
out=[]
def P(*a): s=' '.join(str(x) for x in a); print(s); out.append(s)
src=open('/data/tk590/chk.py').read()
exec(src[src.index('def prep'):src.index('def oos')])
U1=E[E.univ=='U1']; U0=E[(E.univ=='U0')&(E.indep==1)]
# sanity: single features OOS in U1 (sign learned on train half)
for sd in ['long','short']:
    g=U1[U1.side==sd]; r=[]
    for m in ['tpm60','pierce_ep','mv5','cons60','lturn24']:
        for a,b in [('A','B'),('B','A')]:
            tr=g[g.half==a]; te=g[g.half==b]; Xtr,Xte=prep(tr,te,[m]); w=fit(Xtr,tr.y.values.astype(float),1.0)
            r.append(f"{m} {a}->{b} {auc(pred(w,Xte),te.y.values):.3f}")
    P('== U1 single OOS',sd,'; '.join(r))
# transfer: train U0 indep half A (+all features), test U1 half B, and B->A; score deciles vs control
M={'turn24 only':['lturn24'],'turn24+age+natr':['lturn24','age_h','natr_bp'],'8 primary + turn24':PRIM+['lturn24'],'all 39':EXPL}
for lab,cl in [('y','ctrl_o3_x1'),('yf',None)]:
  for sd in ['long','short']:
    for a,b in [('A','B'),('B','A')]:
        tr=U0[(U0.side==sd)&(U0.half==a)]; te=U1[(U1.side==sd)&(U1.half==b)]
        row={}
        for m,cols in M.items():
            Xtr,Xte=prep(tr,te,cols); w=fit(Xtr,tr[lab].values.astype(float),max(1.0,2.0*len(cols))); s=pred(w,Xte)
            row[m]=round(auc(s,te[lab].values),3)
            if m=='all 39' and cl:
                q=pd.qcut(rankdata(s),3,labels=False); t=te.assign(q=q)
                h=t[t.ctrl_n>0].groupby('q').agg(n=('y','size'),y=('y','mean'),c=(cl,'mean'))
                row['tertiles y/ctrl (same eps)']=[f"{r.y*100:.1f}/{r.c*100:.1f}" for _,r in h.iterrows()]
        P(f'== transfer U0indep-{a} -> U1-{b} {sd} {lab}',json.dumps(row,ensure_ascii=False))
# U0 itself: does all-39 score lift control equally? (level-specific or generic volatility)
for sd in ['long','short']:
    tr=U0[(U0.side==sd)&(U0.half=='A')]; te=U0[(U0.side==sd)&(U0.half=='B')]
    Xtr,Xte=prep(tr,te,EXPL); w=fit(Xtr,tr.y.values.astype(float),78.0); s=pred(w,Xte)
    t=te.assign(q=pd.qcut(rankdata(s),5,labels=False)); h=t[t.ctrl_n>0].groupby('q').agg(n=('y','size'),y=('y','mean'),c=('ctrl_o3_x1','mean'))
    P('== U0 A->B all39 quintiles y/ctrl',sd,[f"{r.n} {r.y*100:.2f}/{r.c*100:.2f}" for _,r in h.iterrows()])
    P('   ctrl_o3 stats',te.ctrl_o3_x1.describe().round(3).to_dict())
open('/data/tk590/chk2.out','w').write('\n'.join(out))
