import json, sys, collections
def pct(a,p):
    a=sorted(a); return a[min(len(a)-1,int(len(a)*p))] if a else None
pool=json.load(open(sys.argv[1])); P=set(pool["pool"]); tag=sys.argv[2]; S=float(sys.argv[3])
for kind in ("book","trade","mark"):
    lag=[]; cnt=collections.Counter(); n=0; t0=t1=None; closed=None; resp=0; err=0; peer=None
    for l in open(f"frames/{tag}-{kind}.jsonl"):
        j=json.loads(l)
        if j.get("ev")=="connected": peer=j["peer"]; continue
        if j.get("ev")=="closed": closed=j; continue
        d=json.loads(j["f"]); rx=j["ts"]/1e6
        if d.get("event")=="subscribe":
            resp+=1; err+= d.get("result",{}).get("status")!="success"; continue
        if d.get("event")!="update": continue
        t0=t0 or j["ts"]; t1=j["ts"]
        r=d["result"]
        if kind=="book": cnt[r["s"]]+=1; lag.append(rx-r["t"]); n+=1
        elif kind=="trade":
            for x in r: cnt[x["contract"]]+=1; lag.append(rx-x["create_time_ms"]); n+=1
        else:
            for x in r: cnt[x["contract"]]+=1; lag.append(rx-x["t"]); n+=1
    secs=(t1-t0)/1e9
    v=sorted(cnt.values())
    print(f"{kind}: peer {peer} closed={closed} responses {resp} errors {err}; records {n} = {n/secs:.0f}/s over {secs:.0f}s; symbols with data {len(cnt)}/{len(P)}; per-symbol rate/s p50 {pct(v,.5)/secs:.2f} p90 {pct(v,.9)/secs:.2f} max {v[-1]/secs:.1f}; lag(ms, Python harness) p50 {pct(lag,.5):.0f} p99 {pct(lag,.99):.0f}")
    if kind=="mark": print("   symbols with 1..2 tickers:", sum(1 for x in v if x<=2), " silent symbols:", sorted(P-set(cnt))[:15], len(P-set(cnt)))
    if kind=="book": print("   silent:", len(P-set(cnt)), sorted(P-set(cnt))[:15])
    if kind=="trade": print("   silent:", len(P-set(cnt)))
