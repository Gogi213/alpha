import json,glob,statistics,datetime
import os
P=os.environ.get("TK584_DIR","/tmp/tk584")
D=json.load(open(P+"/runsym.json"))
dirs={"binance":"collect/binance","bingx":"collect/bingx","gate":"collect/gate","okx":"collect/okx","bybit":"turbo-probe/soak-bybit"}
def hm(ms): return datetime.datetime.fromtimestamp(ms/1000,datetime.UTC).strftime("%d %H:%M")
for ex,dr in dirs.items():
    snaps={}
    for f in glob.glob(f"{P}/{dr}/data/pool-????-??-??-*.json"):
        d=json.load(open(f)); snaps[str(d["run_id"])]=d
    for rid,d in sorted(snaps.items()):
        runs=D[ex]; data=runs.get(rid) or runs.get(str(int(rid)-1)) or runs.get(str(int(rid)+1))
        if data is None: print(ex,rid,"no data run"); continue
        S=int(rid)*1000
        init=set(d["pool"]); nrun=sorted(runs,key=int); 
        # end of this run: next run start or data end
        idx=nrun.index(rid) if rid in nrun else None
        endms=int(nrun[idx+1])*1000 if idx is not None and idx+1<len(nrun) else None
        nodata=[s for s in init if s not in data]
        late=[(s,(data[s][1]-S)/1000) for s in init if s in data and data[s][1]-S>120000]
        adds=[(c["symbol"],c["ts_ms"]) for c in d["changes"] if c["op"]=="add" and (endms is None or c["ts_ms"]<endms)]
        add_nodata=[s for s,t in adds if s not in data]
        add_delay=[(data[s][1]-t)/1000 for s,t in adds if s in data and data[s][1]>=t-5000]
        print(ex,hm(S),"init",len(init),"no data at all",len(nodata),"first rec >2min after start",len(late),(max(l for s,l in late) if late else ""),"| adds",len(adds),"no data",len(add_nodata),"delay s med %.1f max %.1f"%(statistics.median(add_delay),max(add_delay)) if add_delay else "")
        if nodata: print("    nodata sample",nodata[:6])
