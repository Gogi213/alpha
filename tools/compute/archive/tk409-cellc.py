import sys
import pandas as pd, numpy as np
from scipy import stats
cols="coin,tag,T,xf,TO,L,A,n_swap,gross_swap,excess_swap,n_gate,gross_gate,excess_gate,vis,n,gross,med,ctrl,excess,ci_lo,ci_hi,net_v,net_d,ref_mk,hit,n_days,good_days,pos_good,gate_mv,dex_mv,n_fund,per_day".split(',')
d=pd.read_csv(''+sys.argv[1]+'',names=cols)
def stat(g,lab):
    n=g.n.sum(); gw=np.average(g.gross,weights=g.n); ew=np.average(g.excess,weights=g.n)
    t,p2=stats.ttest_1samp(g.excess,0); p=p2/2 if g.excess.mean()>0 else 1-p2/2
    tg,pg2=stats.ttest_1samp(g.gross,0); pg=pg2/2 if g.gross.mean()>0 else 1-pg2/2
    print(f'{lab}: монет {len(g)} n {int(n)} валовый_w {gw:.2f} избыток_w {ew:.2f} нетто_вал(−2.6) {gw-2.6:.2f} | кластер по монетам: избыток t {t:.2f} p1 {p:.3f}; валовый t {tg:.2f} p1 {pg:.3f} df {len(g)-1}')
stat(d,'все 9'); 
for x in (['AGENCY'],['ALLINU'],['AGENCY','ALLINU']): stat(d[~d.coin.isin(x)],'без '+'+'.join(x))
best=d.loc[d.gross.idxmax(),'coin']; print('лучшая по валовому:',best); stat(d[d.coin!=best],'без лучшей')
print(d[['coin','n','gross','excess','n_days','pos_good','good_days']].to_string(index=False))
# по суткам
rows=[]
for _,r in d.iterrows():
    for s in str(r.per_day).split(';'):
        p=s.split(':')
        if len(p)==3: rows.append((r.coin,int(p[0]),int(p[1]),float(p[2])))
pdf=pd.DataFrame(rows,columns=['coin','day','n','g'])
def bd(x,lab):
    a=x.groupby('day').apply(lambda g:np.average(g.g,weights=g.n)); print(lab,'сутки>0:',int((a>0).sum()),'из',len(a),{k:round(v,1) for k,v in a.items()})
bd(pdf,'все'); bd(pdf[~pdf.coin.isin(['AGENCY','ALLINU'])],'без AGENCY+ALLINU'); bd(pdf[pdf.coin!=best],'без лучшей')
print('эпизодов в сутках: монета-сутки с эпизодами',len(pdf),'; верхняя граница независимых (≤1 на монету на 8ч-окно, 3 окна/сут)',int(sum(min(r.n,3) for r in pdf.itertuples())))
