import json,collections,statistics as st,sys
d=json.load(open("/tmp/v3.json"));T0=d["s0"];T1=T0+96*900
S=json.load(open("/tmp/stall.json"))
nw=collections.defaultdict(lambda:collections.defaultdict(list))
for day in ("2026-11-14","2026-11-15","2026-11-16"):
    try: f=open(f"/data/netwatch/{day}.jsonl")
    except: continue
    for ln in f:
        try:r=json.loads(ln)
        except: continue
        ts=r["ts"]
        if not T0<=ts<T1: continue
        nw[r["n"]][int(ts-T0)//10].append(r["rtt"])
def stall(ex): return {int(k) for k,v in S[ex].items() if v[1]>2000}
def episodes(s):
    out=[];cur=[]
    for k in sorted(s):
        if cur and k-cur[-1]>6: out.append(cur);cur=[]
        cur.append(k)
    if cur: out.append(cur)
    return out
def summ(name,bins,probe):
    v=[x for k in bins for x in nw[probe].get(k,[])]
    to=sum(1 for x in v if x is None); ok=sorted(x for x in v if x is not None)
    if not ok: print(name,probe,"n=0");return
    q=lambda p: ok[min(len(ok)-1,int(p*len(ok)))]
    print(f"{name:28s} {probe:8s} n={len(v):6d} timeout={to:4d} ({100*to/max(1,len(v)):.2f}%) p50={q(.5):.0f} p90={q(.9):.0f} p99={q(.99):.0f} >1000ms={sum(1 for x in ok if x>1000)}")
allb=set(range(96*90))
for ex in ("binance","gate"):
    s=stall(ex); eps=episodes(s)
    # onset: first 3 bins of each episode (before rolling inflation)
    onset={k for e in eps for k in e[:3]}
    print(ex,"stall bins",len(s),"episodes",len(eps))
    for probe in ("binance","bybit","okx","bingx","gateway","neutral"):
        summ(ex+" stall onset bins",onset,probe)
        summ(ex+" non-stall bins",allb-s-{k+x for k in s for x in range(-6,7)},probe)
