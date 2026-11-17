import json,glob,os,re,datetime
import os
P=os.environ.get("TK584_DIR","/tmp/tk584")
D=json.load(open(P+"/runsym.json"))
T0=int(datetime.datetime(2026,11,15,tzinfo=datetime.UTC).timestamp())
dirs={"binance":"collect/binance","bingx":"collect/bingx","gate":"collect/gate","okx":"collect/okx","bybit":"turbo-probe/soak-bybit"}
def hm(ms): return datetime.datetime.fromtimestamp(ms/1000,datetime.UTC).strftime("%d %H:%M:%S")
out={}
for ex,dr in dirs.items():
    files=sorted(glob.glob(f"{P}/{dr}/data/pool-*-*.json")) # dated
    snaps=[]
    for f in glob.glob(f"{P}/{dr}/data/pool-????-??-??*.json"):
        d=json.load(open(f))
        key=d.get("run_id") or d["ts_ms"]//1000
        snaps.append((key,os.path.basename(f),d))
    seen={}
    for k,b,d in snaps: seen[k]=(b,d)
    ks=sorted(seen)
    print("==",ex)
    for i in range(1,len(ks)):
        if ks[i]<T0: continue
        pb,pd=seen[ks[i-1]]; nb,nd=seen[ks[i]]
        prev_final=set(pd.get("pool_now") or pd["pool"])
        init=set(nd["pool"])
        removed=sorted(prev_final-init); added=sorted(init-prev_final)
        print(hm(ks[i]*1000), nb, "prev_final",len(prev_final),"init",len(init),"removed",len(removed),"added",len(added),"held_from_prev",nd.get("held_from_previous_run"))
        out.setdefault(ex,[]).append({"run":ks[i],"removed":removed,"added":added,"init":sorted(init)})
json.dump(out,open(P+"/pooldiff.json","w"))
