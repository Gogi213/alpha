"""bxfacts: events of the four collectors (gap, timeout, error, slow_pong ...) in 10 s bins with sar (iseg/s relative to median, retrans/s, steal) from $BXFIX/ev, $BXFIX/sar/sa13.csv (sadf -d -- -u -n TCP,ETCP,DEV).
  clusters.py <from epoch s>
"""
import json,glob,os,datetime,collections,statistics,sys
BASE=os.environ.get("BXFIX","/tmp/bxfix"); EV=BASE+"/ev"
roots={"binance":"collect/binance/data","okx":"collect/okx/data","bingx":"collect/bingx/data","bybit":"turbo-probe/soak-bybit/data"}
T0=int(sys.argv[1]) if len(sys.argv)>1 else 1794568469
evs=[]
for ex,r in roots.items():
    for p in glob.glob(f"{EV}/{r}/events-*.jsonl"):
        for ln in open(p,encoding="utf-8"):
            e=json.loads(ln)
            if e["ts_ms"]/1000>=T0: e["ex"]=ex; evs.append(e)
def hm(s): return datetime.datetime.fromtimestamp(s,datetime.UTC).strftime("%H:%M:%S")
# sar
sec=None; iseg={}; retr={}; steal={}
for ln in open(BASE+"/sar/sa13.csv"):
    if ln.startswith("#"):
        sec=ln.split(";")[3]; continue
    f=ln.strip().split(";")
    ts=int(datetime.datetime.strptime(f[2],"%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=datetime.UTC).timestamp())
    if sec=="CPU": steal[ts]=float(f[8])
    elif sec=="active/s": iseg[ts]=float(f[5])
    elif sec=="atmptf/s": retr[ts]=float(f[5])
kinds=("gap","timeout","error","ping_degraded","slow_pong","path_rejected_rtt","entrance_switch")
b=collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
for e in evs:
    if e["ev"] in kinds:
        k=e["ev"]
        if k=="timeout": k="to_"+e.get("kind","?")
        b[e["ts_ms"]//10000*10][e["ex"]][k]+=1
med=statistics.median(iseg.values())
print("sar window", hm(min(iseg)), hm(max(iseg)), "median iseg", med)
for t in sorted(b):
    d=b[t]
    s=" | ".join(f"{ex}:{dict(c)}" for ex,c in sorted(d.items()))
    near=lambda d: d[min(d, key=lambda k: abs(k-(t+5)))] if d and min(abs(k-(t+5)) for k in d) <= 10 else None
    i,r,st=near(iseg),near(retr),near(steal)
    print(hm(t), f"iseg={i and round(i/med,2)} retr={r} steal={st}", s[:230])
