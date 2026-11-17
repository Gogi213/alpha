import json,datetime
import os
P=os.environ.get("TK584_DIR","/tmp/tk584")
D=json.load(open(P+"/runsym.json")); PD=json.load(open(P+"/pooldiff.json"))
T0=int(datetime.datetime(2026,11,15,tzinfo=datetime.UTC).timestamp())
out={}
for ex,runs in sorted(D.items()):
    ids=sorted(runs,key=int)
    pdm={x["run"]:x for x in PD.get(ex,[])}
    for i in range(1,len(ids)):
        S=int(ids[i])
        if S<T0: continue
        prev,cur=runs[ids[i-1]],runs[ids[i]]
        alive={s for s,(n,f,l) in prev.items() if l>=S*1000-600_000}
        dropped=alive-set(cur)
        late={s for s in alive&set(cur) if cur[s][1]-S*1000>600_000}
        # pool-level removed: find nearest pool run id within 5s
        rem=set()
        for r,x in pdm.items():
            if abs(r-S)<=5: rem=set(x["removed"])
        out.setdefault(ex,[]).append({"S":S,"dropped":sorted(dropped),"late":sorted(late),"pool_removed":sorted(rem),"pool_removed_not_in_data_drop":sorted(rem-dropped)})
        print(ex,datetime.datetime.fromtimestamp(S,datetime.UTC).strftime("%d %H:%M"),"data-dropped",len(dropped),"late",len(late),"pool-removed",len(rem),"pool-removed&data-dropped",len(rem&dropped),"pool-removed but data present",len(rem-dropped))
# BingX 17.11 04:12 restart: not in b3 (data to 16.11 23:57); pool-level only
json.dump(out,open(P+"/dropsets.json","w"))
