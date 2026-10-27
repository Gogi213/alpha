import csv,gzip,os,math,sys,json,statistics as S
from collections import defaultdict
MIN=60000
R=("/srv/alpha-sda/candles-1m-aug","/srv/alpha-sda/candles-1m")
def load(sub,sym):
    d={}
    for r in R:
        fn="%s/%s/%s.csv.gz"%(r,sub,sym)
        if os.path.exists(fn):
            with gzip.open(fn,"rt") as f:
                f.readline()
                for l in f:
                    p=l.split(",",7); c=float(p[4])
                    if c>0: d[int(p[0])//MIN]=(c,float(p[6] or 0))
    return d
legs={}
busy=defaultdict(list); ev=[]
for t in ("60","240"):
    for r in csv.DictReader(open("/data/tk433/out/legs-2026-09-th%s.csv"%t,encoding="utf-8")):
        k=(r["биржа"],r["рынок"],r["инструмент"]); a,e=int(r["t_start_ms"])//MIN,int(r["t_peak_ms"])//MIN
        busy[k].append((a,e))
        if r["исключено"]=="" and r.get("представитель")=="1": ev.append((k,a,e,t))
SEP0=int(1756684800000//MIN)  # 2025? fix below
import datetime
SEP0=int(datetime.datetime(2026,9,1,tzinfo=datetime.timezone.utc).timestamp())//60
AUG0=int(datetime.datetime(2026,8,1,tzinfo=datetime.timezone.utc).timestamp())//60
coins=sorted(set(k for k,_,_,_ in ev))
res=[];hourly={}
for k in coins:
    d=load(k[0]+"-"+k[1],k[2])
    if not d: continue
    bz=busy[k]
    def free(m): return not any(a-60<=m<=b+60 for a,b in bz)
    # hourly buckets in Sep, free minutes: turnover sum per (day,hour), and abs ret
    cell=defaultdict(float); cnt=defaultdict(int); ret=defaultdict(list)
    for m,(c,v) in d.items():
        if m<SEP0 or not free(m): continue
        dd,hh=(m-SEP0)//1440,((m%1440)//60)
        cell[(dd,hh)]+=v; cnt[(dd,hh)]+=1
        if m-1 in d: ret[hh].append(abs(math.log(c/d[m-1][0])))
    cells=[(dd,hh,math.log(cell[(dd,hh)]/cnt[(dd,hh)]+1e-9)) for (dd,hh) in cell if cnt[(dd,hh)]>=30 and cell[(dd,hh)]>0]
    if len(cells)<200: continue
    mu=S.mean(x for _,_,x in cells)
    byh=defaultdict(list);byd=defaultdict(list)
    for dd,hh,x in cells: byh[hh].append(x);byd[dd].append(x)
    tot=sum((x-mu)**2 for _,_,x in cells)
    sh=sum(len(v)*(S.mean(v)-mu)**2 for v in byh.values())
    sd=sum(len(v)*(S.mean(v)-mu)**2 for v in byd.values())
    hm=[S.median(v) for v in byh.values() if len(v)>=5]
    hr=[S.median(v) for v in ret.values() if len(v)>=100]
    # drift: Aug free-ish (no legs known) median turnover vs Sep median, daily median spread
    aug=[v for m,(c,v) in d.items() if AUG0<=m<SEP0]
    sep=[v for m,(c,v) in d.items() if m>=SEP0 and free(m)]
    dm=[S.median(v) for v in byd.values()]
    res.append(dict(k="/".join(k),R2_hour=sh/tot,R2_day=sd/tot,hour_range_x=math.exp(max(hm)-min(hm)),
        vol_hour_range_x=(max(hr)/min(hr)) if hr and min(hr)>0 else None,
        aug_sep_x=(S.median(aug)/S.median(sep)) if len(aug)>1000 and S.median(sep)>0 else None,
        day_p90_p10_x=math.exp(sorted(dm)[int(.9*len(dm))]-sorted(dm)[int(.1*len(dm))]) if len(dm)>=10 else None,
        hist_aug=len(aug)>1000))
def q(x): x=sorted(v for v in x if v is not None); return (len(x),round(x[len(x)//4],3),round(x[len(x)//2],3),round(x[3*len(x)//4],3)) if x else None
out={f:q([r[f] for r in res]) for f in ("R2_hour","R2_day","hour_range_x","vol_hour_range_x","aug_sep_x","day_p90_p10_x")}
out["coins"]=len(coins);out["coins_ok"]=len(res)
# coverage alt: events whose coin has >=7d candles before start (past norm)
cov=0;covaug=0
dcache={}
for k,a,e,t in ev:
    pass
out["events"]=len(ev)
json.dump(dict(summary=out,rows=res),open("/data/tk433/j434.json","w"),ensure_ascii=False,indent=1)
print(json.dumps(out,ensure_ascii=False))
