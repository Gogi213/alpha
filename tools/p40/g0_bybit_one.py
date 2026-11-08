"""П-40 G0: проба одного файла Bybit ob200 (zip). Аргумент: путь к zip."""
import zipfile, json, sys
import numpy as np
z = zipfile.ZipFile(sys.argv[1]); f = z.open(z.namelist()[0])
ts=[];cts=[];u=[];typ=[];seq=[]
bids={};asks={};crossed=0;chk=0;ages=[];last=None;bb=ba=None
for line in f:
    m=json.loads(line); d=m["data"]
    ts.append(m["ts"]); cts.append(m.get("cts",0)); u.append(d.get("u",0)); seq.append(d.get("seq",0)); typ.append(m["type"])
    if m["type"]=="snapshot": bids.clear(); asks.clear()
    for p,q in d["b"]:
        if float(q)==0: bids.pop(p,None)
        else: bids[p]=q
    for p,q in d["a"]:
        if float(q)==0: asks.pop(p,None)
        else: asks[p]=q
    if bids and asks:
        nb=max(map(float,bids)); na=min(map(float,asks)); chk+=1
        if nb>=na: crossed+=1
        if (nb,na)!=(bb,ba):
            if last is not None: ages.append(m["ts"]-last)
            last=m["ts"]; bb,ba=nb,na
ts=np.array(ts);cts=np.array(cts);u=np.array(u)
dm=np.array(typ)!="snapshot"
out={"msgs":len(ts),"snapshots":int((~dm).sum()),"ts_nonmono":int((np.diff(ts)<0).sum()),
 "ts_minus_cts_ms_q":[float(x) for x in np.percentile(ts-cts,[1,50,99])],
 "gap_ms_q":[float(x) for x in np.percentile(np.diff(ts),[50,90,99,99.9])],
 "u_gaps":int((np.diff(u[dm])!=1).sum()) if dm.sum()>1 else None,
 "crossed":crossed,"checked":chk,
 "best_change_interval_ms_q":[float(x) for x in np.percentile(ages,[50,99,99.9])]}
print(json.dumps(out,indent=1))
