import json, sys, collections
def pct(a,p):
    a=sorted(a); return a[min(len(a)-1,int(len(a)*p))] if a else None
def load(path):
    bk=collections.defaultdict(list); tr=collections.defaultdict(dict); fr=[]
    for l in open(path):
        j=json.loads(l)
        if "f" not in j: continue
        d=json.loads(j["f"])
        if d.get("event")!="update": continue
        if d["channel"]=="futures.book_ticker":
            r=d["result"]; bk[r["s"]].append((r["t"],r["u"],d["time_ms"],j["ts"]/1e6))
        elif d["channel"]=="futures.trades":
            for r in d["result"]: tr[r["contract"]][r["id"]]=(r["create_time_ms"],d["time_ms"],j["ts"]/1e6,len(d["result"]))
    return bk,tr
A=load(sys.argv[1]); B=load(sys.argv[2])
v=A[0]["BTC_USDT"]
print("BTC book A n",len(v),"t-diff ms p10/p50/p90",pct([b[0]-a[0] for a,b in zip(v,v[1:])],.1),pct([b[0]-a[0] for a,b in zip(v,v[1:])],.5),pct([b[0]-a[0] for a,b in zip(v,v[1:])],.9),"min",min(b[0]-a[0] for a,b in zip(v,v[1:])))
print("  frame time_ms - t p50/p99", pct([x[2]-x[0] for x in v],.5), pct([x[2]-x[0] for x in v],.99))
print("  u step p10/p50/p90", pct([b[1]-a[1] for a,b in zip(v,v[1:])],.1), pct([b[1]-a[1] for a,b in zip(v,v[1:])],.5), pct([b[1]-a[1] for a,b in zip(v,v[1:])],.9))
# are A-only keys interleaved with B? for BTC, check t distribution of A-only vs common
ka={x[1] for x in v}; kb={x[1] for x in B[0]["BTC_USDT"]}
print("BTC common",len(ka&kb),"A only",len(ka-kb),"B only",len(kb-ka))
# frames per second distribution: updates per 10ms bucket
b10=collections.Counter(x[0]//10 for x in v); print("BTC A updates per 10ms bucket: max",max(b10.values()),"hist",collections.Counter(b10.values()))
# u steps between consecutive A-only pushes etc: sample 12 consecutive records
print([ (x[0]%100000,x[1]%100000) for x in v[1000:1012]])
print([ (x[0]%100000,x[1]%100000) for x in B[0]["BTC_USDT"][1000:1012]])
# trade gap
for c,d in A[1].items():
    ids=sorted(d)
    for a,b in zip(ids,ids[1:]):
        if b!=a+1:
            print("gap",c,a,b,"missing",list(range(a+1,b)), "in B:",[i in B[1][c] for i in range(a+1,b)], "times",d[a][0],d[b][0])
# trades: per-frame batch size, frame time lag, ids order in frames
n=collections.Counter(x[3] for d in A[1].values() for x in d.values()); print("trades per frame",sorted(n.items())[:8])
