import numpy as np, json, os
p=os.path.expanduser('~/xdl9/c2t/out/ts_tables')
Z=np.load(p+'.npz'); M=json.load(open(p+'.json',encoding='utf-8'))
ep=Z['ep']; ix={c:i for i,c in enumerate(M['ep_cols'])}
for h in (0,1):
    E=ep[ep[:,ix['half']]==h]
    for gmin,Dg in ((50,30),(50,60),(80,60)):
        m=(E[:,ix['g_in']]>=gmin)&(E[:,ix['D']]>Dg)
        L=E[m]; base=E[E[:,ix['g_in']]>=gmin]
        # stratified expectation by asset: share of Bybit among the asset's episodes (g>=gmin)
        exp=0;var=0
        for a in np.unique(L[:,ix['ai']]):
            b=base[base[:,ix['ai']]==a]; p=b[:,ix['byb']].mean(); k=(L[:,ix['ai']]==a).sum(); exp+=k*p; var+=k*p*(1-p)
        print('FW'[h],'g>=%d D>%d: episodes %d assets %d on Bybit pairs %d ; expected(strat) %.1f +- %.1f ; episodes(g>=%d) total %d'%(gmin,Dg,len(L),len(np.unique(L[:,ix['ai']])),L[:,ix['byb']].sum(),exp,var**.5,gmin,len(base)))
