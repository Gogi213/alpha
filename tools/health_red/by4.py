import json,datetime,bisect,gzip,os,sys
root="/data/turbo-probe/soak-bybit/data"
DAY=sys.argv[1]; shard=sys.argv[2]; tmin=float(sys.argv[3]) # unix start valid
pool=sorted(json.load(open(root+"/pool-2026-11-14.json"))["pool"])
n=-(-len(pool)//80); size=-(-len(pool)//n)
chunks=[pool[i:i+size] for i in range(0,len(pool),size)]
syms=chunks[int(shard[-2:])]
allts=[]
for s in syms:
    raw=gzip.open(f"/tmp/byb/{s}{DAY}.csv.gz","rt").read().splitlines()
    allts+=[float(l.split(",")[0]) for l in raw[1:] if l and l[0].isdigit()]
allts.sort()
d0=int(datetime.datetime.strptime(DAY,"%Y-%m-%d").replace(tzinfo=datetime.timezone.utc).timestamp())
sil=[(allts[i],allts[i+1]) for i in range(len(allts)-1) if allts[i+1]-allts[i]>=3.0 and allts[i]>=tmin]
ev=[]
for ln in open(f"{root}/events-{shard}.jsonl",errors="replace"):
    if '"ev":"gap"' in ln:
        r=json.loads(ln);t=r["ts_ms"]/1000
        if tmin<=t-r["gap_ms"]/1000 and t<d0+86400: ev.append((t-r["gap_ms"]/1000,t,r["gap_ms"]))
f=lambda t:datetime.datetime.utcfromtimestamp(t).strftime("%H:%M:%S.%f")[:-3]
print(DAY,shard,"from",f(tmin),": archive silent>=3s:",len(sil),"| our gap events:",len(ev))
print("our events -> archive trades strictly inside (0.3 s margin):",[ (f(a),g,bisect.bisect_right(allts,b-0.3)-bisect.bisect_left(allts,a+0.3)) for a,b,g in ev])
print("archive silent windows:",[(f(a),round(b-a,2)) for a,b in sil])
