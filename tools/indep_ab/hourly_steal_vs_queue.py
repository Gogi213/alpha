"""Hour boundaries: %steal of the sar bucket containing :00:00 vs prod collector queue_lag_p99 / lag_p99 maxima right after :00."""
import subprocess,re,os,json,glob,datetime,statistics,collections,sys
env={'LC_ALL':'C'}
steal={}   # epoch hour -> (steal_all_at_bucket, steal_prev, steal_next)
day0={}
import time
now=time.time()
for f in sorted(os.listdir('/var/log/sysstat')):
    if not re.match(r'sa\d\d$',f): continue
    mt=os.path.getmtime('/var/log/sysstat/'+f)
    out=subprocess.run(['sar','-f','/var/log/sysstat/'+f,'-u'],capture_output=True,text=True,env=env).stdout
    m=re.search(r'(\d\d)/(\d\d)/(\d\d)',out)
    if not m: continue
    mo,d,y=map(int,m.groups()); date=datetime.date(2000+y,mo,d)
    b=[]
    for l in out.splitlines():
        p=l.split()
        if len(p)==8 and p[1]=='all' and re.match(r'\d\d:\d\d:\d\d$',p[0]):
            h,mi,s=map(int,p[0].split(':'))
            t=int(datetime.datetime(date.year,date.month,date.day,h,mi,s,tzinfo=datetime.timezone.utc).timestamp())
            b.append((t,float(p[6])))
    b.sort()
    for i,(t,st) in enumerate(b):
        H=(t+9)//3600*3600   # bucket label within 0..10 s after the hour => contains :00:00
        if 0<=t-H<=10:
            prev=b[i-1][1] if i>0 else None
            nxt=b[i+1][1] if i+1<len(b) else None
            steal[H]=(st,prev,nxt)
roots={'binance':'/data/collect/binance/data','bybit':'/data/turbo-probe/soak-bybit/data','okx':'/data/collect/okx/data','gate':'/data/collect/gate/data','bingx':'/data/collect/bingx/data'}
q={ex:collections.defaultdict(list) for ex in roots}
for ex,r in roots.items():
    for f in glob.glob(r+'/stats-*.jsonl'):
        for l in open(f,errors='replace').readlines()[-30000:]:
            try:d=json.loads(l)
            except: continue
            ts=d.get('ts_ms')
            if ts is None or d.get('queue_lag_p99_ms') is None: continue
            q[ex][ts//1000].append((d['queue_lag_p99_ms'],d.get('lag_p99_ms') or 0))
def rng(ex,a,b):
    v=[]
    for t in range(a,b):
        v+=q[ex].get(t,[])
    return v
print('hour_utc steal_bucket(prev,this,next) | queue_p99 max in :00..:60 (baseline med -5..-1 min) per exchange')
rows=[]
for H in sorted(steal):
    if H<now-30*3600*2 or H>now: pass
    st,prev,nxt=steal[H]
    r=[datetime.datetime.fromtimestamp(H,datetime.timezone.utc).strftime('%m-%d %H:00'),'steal %.1f (%.1f/%.1f)'%(st,prev or -1,nxt or -1)]
    row={'H':H,'steal':st}
    for ex in roots:
        a=rng(ex,H,H+60); base=rng(ex,H-300,H-60)
        if not a or not base: r.append('%s -'%ex[:3]); continue
        mq=max(x[0] for x in a); bq=statistics.median(x[0] for x in base)
        r.append('%s %.0f(%.0f)'%(ex[:3],mq,bq)); row[ex]=(mq,bq)
    rows.append(row); print(' | '.join(r))
# rank correlation steal vs max queue lag (Binance, Gate, Bybit, OKX, BingX)
def rank(v):
    s=sorted(range(len(v)),key=lambda i:v[i]); r=[0]*len(v)
    for k,i in enumerate(s): r[i]=k
    return r
def spearman(x,y):
    rx,ry=rank(x),rank(y); n=len(x); mx=sum(rx)/n; my=sum(ry)/n
    c=sum((a-mx)*(b-my) for a,b in zip(rx,ry)); sx=sum((a-mx)**2 for a in rx)**.5; sy=sum((b-my)**2 for b in ry)**.5
    return c/(sx*sy)
for ex in roots:
    xs=[(r['steal'],r[ex][0]-r[ex][1]) for r in rows if ex in r]
    if len(xs)>5: print(ex,'n',len(xs),'spearman(steal, queue_p99 rise) = %.2f'%spearman([a for a,b in xs],[b for a,b in xs]))
