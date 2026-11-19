import json,glob,sys,collections,datetime
d=json.load(open("/tmp/v3.json"));T0=d["s0"];T1=T0+96*900
roots=json.load(open("/data/turbo-probe/page/roots.json"))
out={}
for ex,root in roots.items():
    agg=collections.defaultdict(lambda:[0,0.0,0.0,0.0,0.0,0.0,0.0,0.0])
    for f in glob.glob(root+"/stats-*.jsonl"):
        # skip to window: stats files are append-only; read tail only (~24h)
        sz=__import__("os").path.getsize(f)
        with open(f,"rb") as fh:
            fh.seek(max(0,sz-9_000_000)); data=fh.read().decode("utf-8","replace").splitlines()[1:]
        for ln in data:
            i=ln.find('"ts_ms":')
            if i<0: continue
            try: ts=int(ln[i+8:].split("}")[0].split(",")[0])//1000
            except: continue
            if not T0<=ts<T1: continue
            try:r=json.loads(ln)
            except: continue
            k=(ts-T0)//10
            a=agg[k]
            a[0]+=r.get("msgs",0) or 0
            a[1]=max(a[1],r.get("lag_p99_ms") or 0)
            a[2]=max(a[2],r.get("busy_max_us") or 0)
            a[3]=max(a[3],r.get("disk_write_max_us") or 0)
            a[4]=max(a[4],r.get("handoff_max_us") or 0)
            a[5]=max(a[5],r.get("recvq_max") or 0)
            a[6]=max(a[6],r.get("queue_lag_p99_ms") or 0)
            a[7]=max(a[7],r.get("ring_blocked_ms") or 0)
    out[ex]={k:v for k,v in agg.items()}
    print(ex,len(agg),"bins; first/last",min(agg) if agg else None,max(agg) if agg else None,file=sys.stderr)
json.dump(out,open("/tmp/stall.json","w"))
