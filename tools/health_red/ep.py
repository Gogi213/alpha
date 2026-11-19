import json,itertools
D=json.load(open("/tmp/minfeat.json"))
ms=sorted(int(k) for k in D)
ex=["binance","gate","bybit","okx","bingx"]
def bad(e,m,th):
    v=D[str(m)][e]
    return v is not None and (v[0]>0 or v[1]>=th)
for th in (1,100):
    print("minute is bad if lost_ms>0 or late>=%d"%th)
    B={e:{m for m in ms if bad(e,m,th)} for e in ex}
    # restrict to minutes where gate has coverage (gate series exists)
    cov={m for m in ms if D[str(m)]["gate"] is not None}
    print("  bad minutes:",{e:len(B[e]&cov) for e in ex},"of",len(cov))
    for a,b in itertools.combinations(ex,2):
        A=B[a]&cov;C=B[b]&cov
        near=lambda s,m:any((m+x) in s for x in (-1,0,1))
        print(f"  {a:7s}{b:7s} both={len(A&C):3d} expected={len(A)*len(C)/len(cov):6.1f}  | {a} bad minutes with {b} bad within +-1 min: {sum(1 for m in A if near(C,m))}/{len(A)}")
# episodes: merge bad minutes (binance, gate) separated < 5 min
def eps(S,gap=5):
    out=[];cur=[]
    for m in sorted(S):
        if cur and m-cur[-1]>gap: out.append(cur);cur=[]
        cur.append(m)
    if cur: out.append(cur)
    return out
B={e:{m for m in ms if bad(e,m,100)} for e in ex}
cov={m for m in ms if D[str(m)]["gate"] is not None}
EB=eps(B["binance"]&cov);EG=eps(B["gate"]&cov)
import datetime
f=lambda m:datetime.datetime.utcfromtimestamp(m*60).strftime("%H:%M")
print("Binance episodes (late>=100|lost, gaps<5 min merged):",len(EB),[(f(e[0]),len(e)) for e in EB])
print("Gate    episodes:",len(EG),[(f(e[0]),len(e)) for e in EG])
ov=0
for e in EB:
    lo,hi=e[0]-2,e[-1]+2
    if any(not(g[-1]<lo or g[0]>hi) for g in EG): ov+=1
print("Binance episodes overlapping a Gate episode (+-2 min):",ov,"of",len(EB))
