import json,glob,collections,sys,datetime
d=json.load(open("/tmp/v3.json"));T0=d["s0"];T1=T0+86400
S=json.load(open("/tmp/stall.json"))
roots=json.load(open("/data/turbo-probe/page/roots.json"))
for ex in ("binance","gate","bybit","okx","bingx"):
    root=roots[ex]
    bins={int(k):v for k,v in S[ex].items()}
    stall={k for k,v in bins.items() if v[1]>2000}
    near=lambda b: any((b+x) in stall for x in (-1,0,1))
    ip={};to=[]
    for f in glob.glob(root+"/events-*.jsonl"):
        for ln in open(f,errors="replace"):
            if '"ev":"connected"' in ln:
                try:r=json.loads(ln)
                except:continue
                ip[(r["shard"],r["session"])]=r.get("ip")
            elif '"ev":"timeout"' in ln:
                try:r=json.loads(ln)
                except:continue
                if T0<=r["ts_ms"]/1000<T1: to.append(r)
    n=len(to); pong=[r for r in to if r.get("kind")=="pong_only"]; sil=[r for r in to if r.get("kind")=="silent"]
    inst=sum(1 for r in to if near((r["ts_ms"]//1000-T0)//10))
    tc=collections.Counter(r["shard"] for r in to)
    ips=collections.Counter(ip.get((r["shard"],r["session"])) for r in to)
    retr=sum(1 for r in to if (r.get("tcp") or {}).get("retrans",0)>0 or (r.get("tcp") or {}).get("unacked",0)>0 or (r.get("tcp") or {}).get("lost",0)>0)
    print(f"{ex}: timeouts 24h={n} (pong_only {len(pong)}, silent {len(sil)}); in/near stall bins={inst} ({100*inst/max(1,n):.0f}%); with tcp retrans/unacked/lost>0 on our socket={retr}")
    print("   top shards:",tc.most_common(5)," distinct ips:",len(ips)," top ips:",[(k,v) for k,v in ips.most_common(3)])
