import json,itertools,statistics as st
S=json.load(open("/tmp/stall.json"))
ex=list(S)
TH=2000
bins={e:{int(k):v for k,v in S[e].items()} for e in ex}
stall={e:{k for k,v in bins[e].items() if v[1]>TH} for e in ex}
print("stall bins (10 s, lag_p99>2 s):",{e:len(stall[e]) for e in ex}, "of", {e:len(bins[e]) for e in ex})
common=set.intersection(*[set(bins[e]) for e in ex])
print("common bins",len(common))
for a,b in itertools.combinations(ex,2):
    A=stall[a]&common;B=stall[b]&common
    exp=len(A)*len(B)/len(common)
    print(f"{a:7s}{b:7s} A={len(A):4d} B={len(B):4d} both={len(A&B):4d} expected_if_independent={exp:6.1f}")
# widen: within +-3 bins (30 s)
def dil(s,w=3): return {k+d for k in s for d in range(-w,w+1)}
print("any-other-exchange stall within 30 s of a Binance stall bin:")
for e in ex:
    others=[o for o in ex if o!=e]
    for o in others:
        A=stall[e]&common; B=dil(stall[o]&common)
        print(f"  {e}->{o}: {len(A&B)}/{len(A)}",end=";")
    print()
# local metrics in stall vs nonstall bins
names=["msgs","lag_p99","busy_max_us","disk_write_max_us","handoff_max_us","recvq_max","queue_lag_p99","ring_blocked_ms"]
for e in ex:
    s=[bins[e][k] for k in stall[e]]; n=[v for k,v in bins[e].items() if k not in stall[e]]
    if not s: continue
    def q(l,i,p): 
        x=sorted(v[i] for v in l); return x[min(len(x)-1,int(len(x)*p))]
    print(e,"stall n=",len(s),"| nonstall n=",len(n))
    for i in (2,3,4,5,6,7):
        print(f"   {names[i]:18s} stall p50={q(s,i,.5):9.0f} p99={q(s,i,.99):9.0f} max={max(v[i] for v in s):9.0f} | nonstall p50={q(n,i,.5):9.0f} p99={q(n,i,.99):9.0f} max={max(v[i] for v in n):9.0f}")
