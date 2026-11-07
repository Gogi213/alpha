import csv, numpy as np, math
rows=list(csv.DictReader(open('daily_y.csv')))
for V in ('V1','V2'):
    y=np.array([float(r['primary']) for r in rows if r['variant']==V]); n=len(y)
    b=math.ceil(n**(1/3)); nb=math.ceil(n/b); m=y.mean()
    ss=0
    for k in range(nb):
        seg=y[k*b:(k+1)*b]; r=seg.sum()-m*len(seg); ss+=r*r
    var=nb/(nb-1)*ss/(n*n)
    se=var**.5
    print(V,'block',b,'nb',nb,'se_block %.5f'%se,'se_iid %.5f'%(y.std(ddof=1)/n**.5),'ratio',se/(y.std(ddof=1)/n**.5), 'implied var-inflation factor (deff)=',var/(y.var(ddof=1)/n))
    print(' block sums',np.round([y[k*b:(k+1)*b].sum() for k in range(nb)],3))
    # block CI 90% t(6)=1.943
    print(' CI90 block', m-1.943*se, m+1.943*se)
