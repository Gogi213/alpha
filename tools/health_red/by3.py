import json,glob,gzip,io,subprocess,datetime,sys,os,concurrent.futures as cf,bisect
root="/data/turbo-probe/soak-bybit/data"
pool=json.load(open(root+"/pool-2026-11-14.json"))["pool"]; pool=sorted(pool)
n=-(-len(pool)//80); size=-(-len(pool)//n)
chunks=[pool[i:i+size] for i in range(0,len(pool),size)]
print("pool",len(pool),"chunks",len(chunks),[len(c) for c in chunks])
DAY="2026-11-14"
d0=int(datetime.datetime(2026,11,14,tzinfo=datetime.timezone.utc).timestamp())
shard=sys.argv[1]; ci=int(shard[-2:])
syms=chunks[ci]
os.makedirs("/tmp/byb",exist_ok=True)
def get(s):
    p=f"/tmp/byb/{s}{DAY}.csv.gz"
    if not os.path.exists(p) or os.path.getsize(p)==0:
        subprocess.run(["curl","-s","-m","60","-o",p,f"https://public.bybit.com/trading/{s}/{s}{DAY}.csv.gz"])
    try:
        raw=gzip.open(p,"rt").read().splitlines()
    except Exception as e:
        return s,None
    ts=[float(l.split(",")[0]) for l in raw[1:] if l and l[0].isdigit()]
    return s,ts
allts=[];miss=[]
with cf.ThreadPoolExecutor(8) as ex:
    for s,ts in ex.map(get,syms):
        if ts is None: miss.append(s)
        else: allts+=ts
allts.sort()
print("symbols",len(syms),"missing",miss,"trades",len(allts))
# our gap events for this shard on DAY
ev=[]
for ln in open(f"{root}/events-{shard}.jsonl",errors="replace"):
    if '"ev":"gap"' in ln:
        r=json.loads(ln); t=r["ts_ms"]/1000
        if d0<=t<d0+86400: ev.append((t-r["gap_ms"]/1000, t, r["gap_ms"]))
print("our gap events on",DAY,"for",shard,":",len(ev))
def trades_in(a,b):
    return bisect.bisect_right(allts,b)-bisect.bisect_left(allts,a)
bad=0
for a,b,g in ev:
    k=trades_in(a+0.3,b-0.3)   # strictly inside the detected silent window (0.3 s margin each side)
    print(datetime.datetime.utcfromtimestamp(a).strftime("%H:%M:%S.%f")[:-3],"gap_ms",g,"archive trades inside window:",k)
    bad+= k>0
print("events with trades inside window:",bad,"of",len(ev))
# archive silent windows >=3.0 s inside hours 00-24 for reference
sil=[(allts[i],allts[i+1]) for i in range(len(allts)-1) if allts[i+1]-allts[i]>=3.0]
print("archive silent windows >=3 s on",DAY,":",len(sil),"total s:",round(sum(b-a for a,b in sil),1))
mt=0
for a,b,g in ev:
    if any(abs(a-sa)<2.0 for sa,sb in sil): mt+=1
print("our events matched to archive silent windows (+-2 s start):",mt,"of",len(ev))
