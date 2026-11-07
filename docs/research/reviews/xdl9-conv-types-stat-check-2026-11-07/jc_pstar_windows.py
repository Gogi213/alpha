import csv, numpy as np, sys
sys.stdout.reconfigure(encoding='utf-8')
def load(p):
    rows=list(csv.DictReader(open(p,encoding='utf-8')))
    return rows
for V in ('V1','V2'):
    rows=load(f'grid_{V}/trades.csv')
    net=np.array([float(r['net']) for r in rows]); gross=np.array([float(r['gross']) for r in rows])
    fund=np.array([float(r['fund']) for r in rows]); fee=np.array([float(r['fee']) for r in rows])
    code=np.array([r['code'] for r in rows]); hold=np.array([float(r['hold']) for r in rows])
    n=len(rows)
    print(V,'n',n,'net',net.mean(),'gross',gross.mean(),'fund',fund.mean(),'fee',fee.mean())
    from collections import Counter
    print(Counter(code))
    s1=code=='2'
    p=s1.mean()
    mu_s=net[s1].mean(); mu_n=net[~s1].mean()
    print(' p_S1',p,'mu_stop',mu_s,'mu_nonstop',mu_n)
    pstar=mu_n/(mu_n-mu_s)
    print(' p* =',pstar)
    # CI on p (exact-ish Wilson)
    z=1.96
    print(' Wilson', (p+z*z/(2*n)-z*np.sqrt(p*(1-p)/n+z*z/(4*n*n)))/(1+z*z/n),(p+z*z/(2*n)+z*np.sqrt(p*(1-p)/n+z*z/(4*n*n)))/(1+z*z/n))
    assets=set(r['key'] for r in rows if r['code']=='2')
    print(' stop assets',len(assets), sorted(assets))
    allassets=set(r['key'] for r in rows); print(' all assets',len(allassets))
