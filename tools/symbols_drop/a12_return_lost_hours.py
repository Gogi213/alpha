import json,datetime,statistics,pickle
import os
P=os.environ.get("TK584_DIR","/tmp/tk584")
D=json.load(open(P+"/runsym.json")); res=pickle.load(open(P+"/a7.pkl","rb"))
dirs={"binance":"collect/binance","bingx":"collect/bingx","gate":"collect/gate","okx":"collect/okx","bybit":"turbo-probe/soak-bybit"}
for ex in ["bybit","gate","okx","bingx"]:
    runs=D[ex]; ids=sorted(runs,key=int)
    st=json.load(open(f"{P}/{dirs[ex]}/data/pool-state.json"))["last_above"]
    allrows=[(S,d) for (e,S),d in res.items() if e==ex]
    n=0; est=[]; delays=[]; back=0; nowin=0; endmiss=0
    seen=set()
    for S,det in sorted(allrows):
        for (s,e,backt,last_any,tr,qvnow,inpool) in det:
            if s in seen: continue
            seen.add(s); n+=1
            if e is not None: est.append(e)
            # return delay: first record in any run with id >= S after S
            ret=None
            for rid in ids:
                if int(rid)>=S-1 and s in runs[rid]:
                    f=runs[rid][s][1]
                    if f>S*1000+60000: ret=f if ret is None else min(ret,f)
            if ret is not None: delays.append((ret-S*1000)/3600000)
            else: endmiss+=1
            if s in st: nowin+=1
    q=lambda a,p: sorted(a)[int(p*(len(a)-1))]
    print(ex,"unique dropped (restarts 1+2)",n,"| est24 at restart: min %.0f med %.0f p90 %.0f max %.0f"%(min(est),statistics.median(est),q(est,.9),max(est)),
          "| returned in data by 16.11 23:57:",len(delays),"(delay h: med %.1f max %.1f)"%(statistics.median(delays),max(delays)) if delays else "",
          "| not returned by 16.11 23:57:",endmiss,"| in pool-state now:",nowin,"| missing now:",n-nowin)
print("---- symbol-hours lost till 16.11 23:57 UTC")
for ex in ["bybit","gate","okx","bingx"]:
    runs=D[ex]; ids=sorted(runs,key=int)
    end=max(v[2] for r in runs.values() for v in r.values())
    allsyms={s for r in runs.values() for s in r}
    t0=int(datetime.datetime(2026,11,16,tzinfo=datetime.UTC).timestamp())*1000
    total_h=0.0
    for s in allsyms:
        fs=[r[s][1] for r in runs.values() if s in r]; ls=[r[s][2] for r in runs.values() if s in r]
        if max(ls)<t0: continue
        first=max(min(fs),t0)
        total_h+=(end-first)/3600000
    lost=0.0; seen=set()
    for (e,S),det in res.items():
        if e!=ex: continue
        for (s,*_) in det:
            if s in seen: continue
            seen.add(s)
            ret=None
            for rid in ids:
                if int(rid)>=S-1 and s in runs[rid]:
                    f=runs[rid][s][1]
                    if f>S*1000+60000: ret=f if ret is None else min(ret,f)
            lost+=((ret or end)-S*1000)/3600000
    print(ex,"lost symbol-hours %.0f of %.0f (%.1f%%) on 16.11"%(lost,total_h,lost/total_h*100))
