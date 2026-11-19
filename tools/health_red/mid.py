import json,glob,sys,os,collections,datetime
roots=json.load(open("/data/turbo-probe/page/roots.json"))
T=int(sys.argv[1]); a,b=T-30,T+60
for ex,root in roots.items():
    agg=collections.defaultdict(lambda:[0,0,0,0,0,0])
    for f in glob.glob(root+"/stats-*.jsonl"):
        sz=os.path.getsize(f)
        # file spans ~3 days; read whole for older T
        with open(f,"rb") as fh: L=fh.read().decode("utf-8","replace").splitlines()
        for ln in L:
            i=ln.find('"ts_ms":')
            if i<0: continue
            try: ts=int(ln[i+8:].split("}")[0].split(",")[0])//1000
            except: continue
            if not a<=ts<b: continue
            try:r=json.loads(ln)
            except: continue
            k=(ts-a)//10*10+a
            g=agg[k]
            g[0]+=r.get("msgs",0) or 0
            g[1]=max(g[1],r.get("lag_p99_ms") or 0)
            g[2]=max(g[2],r.get("queue_lag_p99_ms") or 0)
            g[3]=max(g[3],r.get("busy_max_us") or 0)
            g[4]=max(g[4],r.get("disk_write_max_us") or 0)
            g[5]=max(g[5],r.get("handoff_max_us") or 0)
    print("==",ex,"msgs lagp99 qlagp99 busy_us diskw_us handoff_us")
    for k in sorted(agg):
        print(" ",datetime.datetime.utcfromtimestamp(k).strftime("%H:%M:%S"),*agg[k])
