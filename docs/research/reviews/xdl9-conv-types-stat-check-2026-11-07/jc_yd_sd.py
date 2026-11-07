import csv, numpy as np, sys
rows=list(csv.DictReader(open('daily_y.csv')))
for V in ('V1','V2'):
    y=np.array([float(r['primary']) for r in rows if r['variant']==V]); n=len(y)
    m=y.mean(); sd=y.std(ddof=1); se=sd/np.sqrt(n)
    ac=[np.corrcoef(y[:-k],y[k:])[0,1] for k in (1,2,3)]
    print(V,'n',n,'mean %.5f sd %.5f se %.5f t %.3f'%(m,sd,se,m/se),'ac1-3',np.round(ac,3))
    # Student t crit 20 df
    tc=2.0860
    print('  CI95 t', m-tc*se, m+tc*se)
    # Newey-West lag 1
    e=y-m
    g0=(e*e).sum()/n; g1=(e[1:]*e[:-1]).sum()/n
    v_nw=(g0+2*(1-1/2)*g1)/n
    print('  NW1 se',np.sqrt(v_nw), 'n_eff(ar1)', n*(1-ac[0])/(1+ac[0]))
    # days with positive
    print('  positive days',(y>0).sum(),'of',n)
