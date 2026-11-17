import json,glob,datetime,collections
import os
P=os.environ.get("TK584_DIR","/tmp/tk584")
D=json.load(open(P+"/runsym.json")); DS=json.load(open(P+"/dropsets.json")); R=json.load(open(P+"/rest-now.json"))
dirs={"binance":"collect/binance","bingx":"collect/bingx","gate":"collect/gate","okx":"collect/okx","bybit":"turbo-probe/soak-bybit"}
def tradable(ex,v):
    if ex=="binance": return v["status"]=="TRADING" and v["ctype"]=="PERPETUAL" and v["quote"]=="USDT"
    if ex=="bybit": return v["status"]=="Trading" and v["ctype"]=="LinearPerpetual" and v["quote"]=="USDT"
    if ex=="okx": return v["status"]=="live" and v["ctype"]=="linear" and v["quote"]=="USDT"
    if ex=="bingx": return v["status"]==1 and v["quote"]=="USDT"
    if ex=="gate": return v["status"]=="trading"
T0=int(datetime.datetime(2026,11,15,tzinfo=datetime.UTC).timestamp())*1000
for ex,dr in dirs.items():
    runs=D[ex]; ids=sorted(runs,key=int)
    sym={}  # symbol -> (first,last) over runs from 15.11
    allsym={}
    for rid in ids:
        for s,(n,f,l) in runs[rid].items():
            a=allsym.get(s); allsym[s]=(f,l) if a is None else (min(a[0],f),max(a[1],l))
    end=max(l for _,l in allsym.values())
    miss={s for s,(f,l) in allsym.items() if l<end-300000}
    drop=set(); 
    for r in DS.get(ex,[]): drop|=set(r["dropped"])
    # removed by refresh (hysteresis expiry or delisted) from pool files changes
    rem=set()
    for f in glob.glob(f"{P}/{dr}/data/pool-????-??-??-*.json"):
        d=json.load(open(f))
        for c in d["changes"]:
            if c["op"]=="remove": rem.add(c["symbol"])
    a=miss&drop; b=(miss-drop)&rem; c=miss-drop-rem
    nt=[s for s in miss if not (s in R[ex] and tradable(ex,R[ex][s]))]
    print(ex,"symbols with data since run list",len(allsym),"| last record >5min before end:",len(miss),"= dropped at restart & not back",len(a),"+ removed by refresh later",len(b),"+ other",len(c),"| of the miss: tradable on REST now",len(miss)-len(nt),", not tradable now",len(nt))
    if c: print("   other:",sorted(c)[:10], [ (s,R[ex].get(s,{}).get("status")) for s in sorted(c)[:6]])
    if nt: print("   not tradable now:",[(s,R[ex].get(s,{}).get("status")) for s in sorted(nt)[:8]])
print("---- miss by last run")
for ex,dr in dirs.items():
    runs=D[ex]; ids=sorted(runs,key=int)
    allsym={}
    for rid in ids:
        for s,(n,f,l) in runs[rid].items():
            a=allsym.get(s); allsym[s]=(f,l,rid) if a is None or l>a[1] else (min(a[0],f),a[1],a[2])
    end=max(v[1] for v in allsym.values())
    miss={s:v for s,v in allsym.items() if v[1]<end-300000}
    c=collections.Counter()
    for s,(f,l,rid) in miss.items(): c[datetime.datetime.fromtimestamp(int(rid),datetime.UTC).strftime("%d %H:%M")]+=1
    print(ex,dict(sorted(c.items())))
