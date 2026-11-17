import json,datetime,collections,statistics
import os
P=os.environ.get("TK584_DIR","/tmp/tk584")
D=json.load(open(P+"/runsym.json")); DS=json.load(open(P+"/dropsets.json")); C=json.load(open(P+"/candles.json")); R=json.load(open(P+"/rest-now.json"))
dirs={"binance":"collect/binance","bingx":"collect/bingx","gate":"collect/gate","okx":"collect/okx","bybit":"turbo-probe/soak-bybit"}
def tradable(ex,v):
    if ex=="binance": return v["status"]=="TRADING" and v["ctype"]=="PERPETUAL" and v["quote"]=="USDT"
    if ex=="bybit": return v["status"]=="Trading" and v["ctype"]=="LinearPerpetual" and v["quote"]=="USDT"
    if ex=="okx": return v["status"]=="live" and v["ctype"]=="linear" and v["quote"]=="USDT"
    if ex=="bingx": return v["status"]==1 and v["quote"]=="USDT"
    if ex=="gate": return v["status"]=="trading"
def est24(rows,S):
    t1=S*1000; t0=t1-86400000; tot=0.0
    for t,v in rows:
        ov=max(0,min(t+3600000,t1)-max(t,t0))/3600000
        tot+=ov*v
    return tot
def hm(ms): return datetime.datetime.fromtimestamp(ms/1000,datetime.UTC).strftime("%d %H:%M")
res={}
for ex,rows in sorted(DS.items()):
    runs=D[ex]; ids=sorted(runs,key=int)
    st=json.load(open(f"{P}/{dirs[ex]}/data/pool-state.json"))["last_above"]
    for r in rows:
        S=r["S"]; i=ids.index(str(S)) if str(S) in ids else None
        if not r["dropped"] and not r["late"]: continue
        later=ids[i+1:] if i is not None else []
        cat=collections.Counter(); detail=[]
        for s in r["dropped"]:
            cs=C.get(ex,{}).get(s)
            e=est24(cs,S) if isinstance(cs,list) and cs else None
            # back later in data?
            back=None
            for rid in later:
                if s in runs[rid]: back=runs[rid][s][1]; break
            last_any=max((runs[rid][s][2] for rid in ids if s in runs[rid]),default=None)
            v=R[ex].get(s); tr=bool(v and tradable(ex,v))
            detail.append((s,e,back,last_any,tr,v["qv"] if v else None,s in st))
        res[(ex,S)]=detail
        below=[d for d in detail if d[1] is not None and d[1]<300000]
        above=[d for d in detail if d[1] is not None and d[1]>=300000]
        nocand=[d for d in detail if d[1] is None]
        backed=[d for d in detail if d[2] is not None]
        notrad=[d for d in detail if not d[4]]
        stillnot=[d for d in detail if d[2] is None]
        print(f"{ex} {hm(S*1000)}: dropped {len(detail)} | est24<300k {len(below)}, >=300k {len(above)}, no candles {len(nocand)} | back in later runs {len(backed)}, never back in data(till 16.11 23:57) {len(stillnot)} | not tradable on REST now {len(notrad)} | in pool-state now {sum(1 for d in detail if d[6])}")
        if above: print("   est>=300k:",[(d[0],round(d[1]/1e3)) for d in above][:12])
import pickle; pickle.dump(res,open(P+"/a7.pkl","wb"))
