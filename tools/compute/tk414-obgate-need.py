import csv,os,sys
from datetime import datetime,timezone
need={}
for r in csv.DictReader(open("flow/flowgate2-events.csv",encoding="utf-8")):
    if r["рынок"]!="futures_usdt" or r["вариант"]!="5": continue
    t=int(datetime.strptime(r["t_событие"],"%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc).timestamp())
    mb=int(r["минB"]) if r["минB"] else 0
    lo=(t-mb*60-360*60-3600)//3600; hi=t//3600
    need.setdefault(r["инструмент"],set()).update(range(lo,hi+1))
tot=0;sz=0
w=open(sys.argv[1],"w") if len(sys.argv)>1 else None
for s,hs in need.items():
    d="/data/tk415/ob/gate/gate-fut/"+s
    for h in sorted(hs):
        n=datetime.fromtimestamp(h*3600,timezone.utc).strftime("%Y%m%d%H")
        p=f"{d}/{s}-{n}.csv.gz"
        if os.path.exists(p):
            tot+=1; sz+=os.path.getsize(p)
            if w: w.write(f"{p}\t{s}/{n}.csv\n")
print(len(need),tot,sz/1e9)
