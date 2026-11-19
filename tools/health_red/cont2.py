import json,statistics as st
d=json.load(open("/tmp/v3.json"));A=json.load(open("/tmp/slotsys.json"));S=json.load(open("/tmp/stall.json"))
cpu={}
for i,a in A.items():
    c=[u+n+s for u,n,s in zip(a["user"],a["nice"],a["sys"])]; cpu[int(i)]=sum(c)/len(c)
slots=[i for i in range(2,96) if i in cpu]
hi=[i for i in slots if cpu[i]>=80]; mid=[i for i in slots if 50<=cpu[i]<80]; lo=[i for i in slots if cpu[i]<50]
print("slots: cpu>=80:",len(hi),"50-80:",len(mid),"<50:",len(lo))
for ex in S:
    bins={int(k):v for k,v in S[ex].items()}
    def frac(group,idx_metric,th):
        n=t=0
        for i in group:
            for b in range(i*90,(i+1)*90):
                v=bins.get(b)
                if v is None: continue
                n+=1;t+=v[idx_metric]>th
        return (100*t/n if n else float('nan')),n
    out=[]
    for name,g in (("cpu>=80",hi),("50-80",mid),("<50",lo)):
        f,n=frac(g,1,2000); q,_=frac(g,6,1000); b,_=frac(g,2,10000)
        out.append(f"{name}: lag>2s {f:5.2f}% | qlag>1s {q:5.2f}% | busy>10ms {b:5.2f}%")
    print(f"{ex:8s}"," || ".join(out))
