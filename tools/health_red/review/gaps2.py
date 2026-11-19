import subprocess,glob,collections,sys,datetime,json,re
lo=(1794700800+14*3600+13*60+50)*1000; hi=lo+240*1000
out=[]
for f in sorted(glob.glob(sys.argv[1]+"/trade*-*.tsv.zst")):
    shard=re.search(r"(trade\d+)",f).group(1)
    p=subprocess.Popen(["zstdcat",f],stdout=subprocess.PIPE,text=True,bufsize=1<<20)
    rx=0; last={}; ids=collections.defaultdict(list)
    for ln in p.stdout:
        c=ln.split("\t")
        try: rx+=int(c[1])
        except: continue
        if c[0]!="t": continue
        sym=c[4]; a=int(c[5]) if sym not in last else last[sym]+1+int(c[5]); last[sym]=a
        E=rx//1000000-int(c[2])
        if lo<=E<hi: ids[sym].append((a,E))
    p.wait()
    miss=0; spans=[]
    for sym,v in ids.items():
        v.sort()
        for (x,ex),(y,ey) in zip(v,v[1:]):
            if y>x+1: miss+=y-x-1; spans.append((ex,ey))
    f2=lambda t: datetime.datetime.utcfromtimestamp(t/1000).strftime("%H:%M:%S.%f")[:12]
    if spans: print(shard,"missing",miss,"gaps",len(spans),"E span of gaps",f2(min(s[0] for s in spans)),"-",f2(max(s[1] for s in spans)))
    else: print(shard,"missing 0")
    ev=sys.argv[1].rsplit("/",1)[0]+f"/events-{shard}.jsonl"
    for ln in open(ev):
        i=ln.find('"ts_ms":'); 
        try: ts=int(re.search(r'"ts_ms":(\d+)\}?\s*$',ln.strip()).group(1))
        except: continue
        if lo-60000<=ts<hi and re.search(r'"ev":"(path_switch|connected|closed|gap|gap_end|ping_degraded|replace_deferred|error|ws_end)"',ln):
            print("   ",f2(ts),re.search(r'"ev":"(\w+)"',ln).group(1),(re.search(r'"(old_session|session)":(\d+)',ln) or [None,None,""])[0] if False else ln[:0])
