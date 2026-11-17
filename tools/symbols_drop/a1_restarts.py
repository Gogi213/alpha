import json,datetime,os
D=json.load(open(os.environ.get("TK584_DIR","/tmp/tk584")+"/runsym.json"))
T0=int(datetime.datetime(2026,11,15,tzinfo=datetime.UTC).timestamp())
def hm(ms): return datetime.datetime.fromtimestamp(ms/1000,datetime.UTC).strftime("%d %H:%M:%S")
res={}
for ex,runs in sorted(D.items()):
    ids=sorted(runs,key=int)
    print("==",ex)
    for i in range(1,len(ids)):
        S=int(ids[i])
        if S<T0: continue
        prev,cur=runs[ids[i-1]],runs[ids[i]]
        alive={s for s,(n,f,l) in prev.items() if l>=S*1000-600_000}
        ended={s for s in prev if s not in alive}
        dropped=alive-set(cur)
        late={s:(cur[s][1]-S*1000)/60000 for s in alive&set(cur) if cur[s][1]-S*1000>600_000}
        new=set(cur)-set(prev)
        print(f"restart {hm(S*1000)}: alive_before {len(alive)}, in_new {len(cur)}, dropped {len(dropped)}, late(>10min) {len(late)}, new(not in prev run) {len(new)}, prev_ended_before {len(ended)}")
