import json, sys, collections
def pct(a,p):
    a=sorted(a); return a[min(len(a)-1,int(len(a)*p))] if a else None
def load(path):
    bk={}; tr={}; tk={}; lags={"b":[],"t":[],"k":[]}; nbk=0; ntr=0; ntk=0; first={}
    for l in open(path):
        j=json.loads(l)
        if "f" not in j: continue
        d=json.loads(j["f"]); rx=j["ts"]/1e6
        if d.get("event")!="update": continue
        ch=d["channel"]
        if ch=="futures.book_ticker":
            r=d["result"]; nbk+=1; bk.setdefault((r["s"],r["u"]),(r["t"],r["b"],r["B"],r["a"],r["A"],rx)); lags["b"].append(rx-r["t"])
        elif ch=="futures.trades":
            for r in d["result"]:
                ntr+=1; tr.setdefault((r["contract"],r["id"]),(r["create_time_ms"],r["price"],r["size"],r.get("is_internal"),rx)); lags["t"].append(rx-r["create_time_ms"])
        elif ch=="futures.tickers":
            for r in d["result"]:
                ntk+=1; tk.setdefault((r["contract"],r["t"]),(r["mark_price"],r["index_price"],r["funding_rate"],rx)); lags["k"].append(rx-r["t"])
    return bk,tr,tk,lags,(nbk,ntr,ntk)
A=load(sys.argv[1]); B=load(sys.argv[2])
for name,i in (("book (sym,u)",0),("trade (contract,id)",1),("ticker (contract,t)",2)):
    a,b=A[i],B[i]; ka,kb=set(a),set(b)
    com=ka&kb
    diff=sum(1 for k in com if a[k][:-1]!=b[k][:-1])
    print(f"{name}: A {len(ka)} B {len(kb)} common {len(com)} onlyA {len(ka-kb)} onlyB {len(kb-ka)} value-diff-on-common {diff}")
print("raw counts book/trade/tick A",A[4],"B",B[4])
for k,n in (("b","book"),("t","trade"),("k","ticker")):
    print(n,"lag p50/p99/max A",pct(A[3][k],.5),pct(A[3][k],.99),max(A[3][k]),"B",pct(B[3][k],.5),pct(B[3][k],.99),max(B[3][k]))
# arrival-order: who first on common book keys
fa=sum(1 for k in set(A[0])&set(B[0]) if A[0][k][-1]<B[0][k][-1]); n=len(set(A[0])&set(B[0])); print("book A first",fa,"of",n)
# duplicate deliveries within one connection
# ticker cadence per contract
tk=collections.defaultdict(list)
for (c,t),v in A[2].items(): tk[c].append(t)
iv=[]; 
for c,ts in tk.items():
    ts.sort(); iv+= [b-a for a,b in zip(ts,ts[1:])]
print("ticker interval p50/p90/p99",pct(iv,.5),pct(iv,.9),pct(iv,.99)); print("tickers per contract per 120s p50",pct([len(v) for v in tk.values()],.5), "contracts",len(tk))
# mark change rate
last={}; ch=0; nn=0
for (c,t),v in sorted(A[2].items(), key=lambda x:(x[0][0],x[0][1])):
    if c in last:
        nn+=1; ch+= (last[c]!=v[:3])
    last[c]=v[:3]
print("ticker records with (mark,index,funding) changed vs previous",ch,"of",nn)
# trade ids continuity per contract in A
ids=collections.defaultdict(list)
for (c,i),v in A[1].items(): ids[c].append(i)
gap=0; tot=0; inv=0
for c,v in ids.items():
    v.sort(); gap+=sum(1 for a,b in zip(v,v[1:]) if b!=a+1); tot+=len(v)
print("trade id gaps (A)",gap,"of",tot)
# is_internal / empty side
print("is_internal count A", sum(1 for v in A[1].values() if v[3]), "empty bid/ask", sum(1 for v in A[0].values() if v[1]=="" or v[3]==""))
# book per-symbol rate
c=collections.Counter(s for (s,u) in A[0]); print("book rate top5 /s", [(s,round(n/120,1)) for s,n in c.most_common(5)], "min", min(c.values()))
