import json, glob, collections
ks={"book":collections.Counter(),"trade":collections.Counter(),"tick":collections.Counter(),"top":collections.Counter(),"nres":collections.Counter()}
ex={}
sizes=collections.Counter(); decs=collections.Counter(); neg0=0; zero=0; n=0; internal=0
for p in glob.glob("frames/w1-*.jsonl")+glob.glob("frames/two1-*.jsonl")+glob.glob("frames/lim-all*.jsonl")+glob.glob("frames/e0.jsonl"):
    for l in open(p):
        j=json.loads(l)
        if "f" not in j: continue
        d=json.loads(j["f"])
        if d.get("event")!="update": continue
        ks["top"][tuple(d.keys())]+=1
        r=d["result"]
        if d["channel"]=="futures.book_ticker": ks["book"][tuple(r.keys())]+=1
        elif d["channel"]=="futures.trades":
            ks["nres"][len(r)]+=1
            for x in r:
                ks["trade"][tuple(x.keys())]+=1; n+=1
                s=x["size"]; sizes[type(s).__name__]+=1
                if "." in s: decs["dec"]+=1; ex.setdefault("dec",x)
                if s=="0": zero+=1
                if x.get("is_internal"): internal+=1; ex.setdefault("internal",x)
        elif d["channel"]=="futures.tickers":
            for x in r: ks["tick"][tuple(x.keys())]+=1; ex.setdefault("tick_"+str(len(x)),x)
for k,v in ks.items(): print(k,len(v),[ (len(a) if k in("tick",) else a, c) for a,c in v.most_common(4)])
print("trades",n,"size types",sizes,"decimal sizes",decs,"zero-size",zero,"internal",internal)
print(ex.get("dec"),ex.get("internal"))
t=[k for k in ex if k.startswith("tick_")]; print(t)
for k in ks["tick"]:
    print(len(k), k)
