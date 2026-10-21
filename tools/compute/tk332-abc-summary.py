import sys,glob,csv,json,collections,statistics as st
R='/data/tk0332-b/'
sets={'A':'outA','B':'outB4','C-3600':'outC-3600','C+3600':'outC3600','C-7200':'outC-7200','C+7200':'outC7200'}
res={}
for k,d in sets.items():
    fs=glob.glob(R+d+'/run-*/out/*/s0/rounds.csv')
    days=collections.defaultdict(float); n=collections.Counter(); syms=set(); nd=set()
    for f in fs:
        for r in csv.DictReader(l for l in open(f) if not l.startswith('#')):
            pass
            q=float(r['qty'] or 0)*float(r['entry_vwap'] or 0)
            if q<=0: continue
            usd=q*float(r['net_bps'])/1e4
            day=r['day_utc']; days[day]+=usd; n[day[:7]]+=1; syms.add(r['symbol'])
    res[k]=(days,n,len(fs),len(syms))
mons=sorted({d[:7] for k in res for d in res[k][0]})
def sr(x): return st.mean(x)/st.pstdev(x)*(365**.5) if len(x)>2 and st.pstdev(x)>0 else float('nan')
print('files',{k:v[2] for k,v in res.items()},'syms',{k:v[3] for k,v in res.items()})
print('mon   '+'  '.join(f'{k:>16}' for k in res))
for m in mons:
    print(m,'  '.join(f"{sum(v for d,v in res[k][0].items() if d[:7]==m):9.0f}/{res[k][1][m]:5d}" for k in res))
print('ALL  ','  '.join(f"{sum(res[k][0].values()):9.0f}/{sum(res[k][1].values()):5d}" for k in res))
print('SR_day','  '.join(f"{sr(list(res[k][0].values())):.2f}" for k in res))
print('months>0','  '.join(str(sum(1 for m in mons if sum(v for d,v in res[k][0].items() if d[:7]==m)>0)) for k in res))
# A на символах, где у B/C есть хоть одна сделка (общий пул по сигналам)
import glob
def load(d):
    out=[]
    for f in glob.glob(R+d+'/run-*/out/*/s0/rounds.csv'):
        for r in csv.DictReader(l for l in open(f) if not l.startswith('#')):
            q=float(r['qty'] or 0)*float(r['entry_vwap'] or 0)
            if q>0: out.append((r['symbol'],r['day_utc'],q*float(r['net_bps'])/1e4))
    return out
rows={k:load(d) for k,d in sets.items()}
cs=set(s for k in rows if k!='A' for s,_,_ in rows[k])
print('common syms',len(cs))
for k in rows:
    sel=[x for x in rows[k] if x[0] in cs]
    print(k,'n',len(sel),'sum',round(sum(x[2] for x in sel)),'m>0',sum(1 for m in mons if sum(x[2] for x in sel if x[1][:7]==m)>0),'avg_bps/$',round(sum(x[2] for x in sel)/max(1,len(sel)),2))
