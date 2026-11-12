import sys,numpy as np,pandas as pd
from scipy import stats
cols="coin,tag,T,xf,TO,L,A,n_swap,gross_swap,excess_swap,n_gate,gross_gate,excess_gate,vis,n,gross,med,ctrl,excess,ci_lo,ci_hi,net_v,net_d,ref_mk,hit,n_days,good_days,pos_good,gate_mv,dex_mv,n_fund,per_day".split(',')
d=pd.read_csv('data/tk409/cellc_p.csv',names=cols)
rows=[]
for _,r in d.iterrows():
    for s in str(r.per_day).split(';'):
        p=s.split(':')
        if len(p)==3: rows.append((r.coin,int(p[0]),int(p[1]),float(p[2])))
x=pd.DataFrame(rows,columns=['coin','day','n','g'])
def t(y,lab):
    a=y.g.values; w=y.n.values
    m=np.average(a,weights=w); u=a.mean()
    tt,p2=stats.ttest_1samp(a,0); p=p2/2 if u>0 else 1-p2/2
    # перестановка знака (по кластерам)
    rng=np.random.default_rng(1); obs=u; cnt=0
    for _ in range(20000):
        if (a*rng.choice([-1,1],len(a))).mean()>=obs: cnt+=1
    # бутстрап по кластерам, веса n
    bs=[np.average(a[i],weights=w[i]) for i in (rng.integers(0,len(a),len(a)) for _ in range(5000))]
    print(f'{lab}: кластеров {len(a)} n {w.sum()} взв.валовый {m:.2f} невзв.среднее {u:.2f} t {tt:.2f} p1 {p:.3f} перест.p1 {(cnt+1)/20001:.3f} бутстрап 95% [{np.percentile(bs,2.5):.2f}; {np.percentile(bs,97.5):.2f}] | >0 в {int((a>0).sum())}/{len(a)}')
t(x,'все 9')
t(x[x.coin!='ALLINU'],'без лучшей (ALLINU)')
t(x[~x.coin.isin(['AGENCY','ALLINU'])],'без AGENCY+ALLINU')
# кластер по монета-сутки, вес n, и двойной: сутки как кластер
a=x.groupby('day').apply(lambda g:np.average(g.g,weights=g.n)); print('по суткам',{k:round(v,1) for k,v in a.items()})
