import json,glob,statistics
import os
P=os.environ.get("TK584_DIR","/tmp/tk584")
PD=json.load(open(P+"/pooldiff.json"))
dirs={"binance":"collect/binance","bingx":"collect/bingx","gate":"collect/gate","okx":"collect/okx","bybit":"turbo-probe/soak-bybit"}
for ex,dr in dirs.items():
    snaps={}
    for f in glob.glob(f"{P}/{dr}/data/pool-????-??-??-*.json"):
        d=json.load(open(f)); snaps[d["run_id"]]=d
    for x in PD.get(ex,[]):
        d=snaps.get(x["run"]) 
        if d is None or not x["removed"]: continue
        rem=set(x["removed"]); S=x["run"]*1000
        adds=[(c["ts_ms"]-S)/60000 for c in d["changes"] if c["op"]=="add" and c["symbol"] in rem]
        rms=[(c["ts_ms"]-S)/60000 for c in d["changes"] if c["op"]=="remove"]
        nowin=set(d["pool_now"]) & rem
        print(ex,x["run"],"removed at restart",len(rem),"re-added by refresh within run",len(adds),"(min after start: min %.1f med %.1f max %.1f)"%(min(adds),statistics.median(adds),max(adds)) if adds else "", "| in pool_now of that run at its end/now",len(nowin))
