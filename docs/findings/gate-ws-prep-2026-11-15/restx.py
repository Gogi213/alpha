import json, urllib.request, time, sys, collections
H={"User-Agent":"alpha-collector/gate-check","X-Gate-Size-Decimal":"1"}
def get(u):
    r=urllib.request.urlopen(urllib.request.Request(u,headers=H),timeout=30); return json.load(r), r.headers
def rest_trades(c,t0,t1):
    """trades with create_time in [t0,t1] (seconds, ints): page backwards by narrowing `to`"""
    out={}; to=t1; n=0
    while True:
        d,h=get(f"https://api.gateio.ws/api/v4/futures/usdt/trades?contract={c}&limit=1000&from={t0}&to={to}"); n+=1
        for x in d: out[x["id"]]=x
        if len(d)<1000: break
        to=int(min(x["create_time"] for x in d))+1   # boundary second re-read: ids dedup
        if to<t0: break
    return out,n
c=sys.argv[1]; t0=int(sys.argv[2]); t1=int(sys.argv[3])
t=time.time(); r,n=rest_trades(c,t0,t1); print(c,"REST trades",len(r),"requests",n,"took",round(time.time()-t,1),"s")
ids=sorted(r); print("id gaps in REST", sum(1 for a,b in zip(ids,ids[1:]) if b!=a+1), "sample",list(r.values())[0])
print("size types",collections.Counter(type(x["size"]).__name__ for x in r.values()), "decimal", sum(1 for x in r.values() if isinstance(x["size"],str) and "." in x["size"]))
json.dump({str(k):v for k,v in r.items()},open(f"frames/rest-{c}-{t0}.json","w"))
