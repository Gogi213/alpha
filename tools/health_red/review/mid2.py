import json,glob,os,sys,datetime
roots=json.load(open("/data/turbo-probe/page/roots.json"))
targets=[int(x) for x in sys.argv[1:]]
for t in targets:
    print("==",datetime.datetime.utcfromtimestamp(t).strftime("%d.%m %H:%M:%S"))
    for ex,root in roots.items():
        best={}
        for f in glob.glob(root+"/stats-*.jsonl"):
            for ln in open(f,errors="replace"):
                i=ln.find('"ts_ms":')
                if i<0: continue
                try: ts=int(ln[i+8:].split("}")[0].split(",")[0])//1000
                except: continue
                if not t-20<=ts<t+40: continue
                r=json.loads(ln); k=(ts-t)//10
                b=best.setdefault(k,[0,0,0])
                b[0]=max(b[0],r.get("lag_p99_ms") or 0); b[1]=max(b[1],r.get("busy_max_us") or 0); b[2]=max(b[2],r.get("disk_write_max_us") or 0)
        print(f"  {ex:8s}"," ".join(f"[{k*10:+d}s lag {v[0]/1000:.1f} busy {v[1]/1000:.1f} disk {v[2]/1000:.1f}]" for k,v in sorted(best.items())))
