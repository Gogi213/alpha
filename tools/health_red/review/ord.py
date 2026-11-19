import json,glob,datetime,collections
d=json.load(open("/tmp/v3.json"));T0=d["s0"];T1=T0+96*900
S=json.load(open("/tmp/stall.json"))
nw=[]
for day in ("2026-11-15",):
    for ln in open(f"/data/netwatch/{day}.jsonl"):
        r=json.loads(ln)
        if r["n"]=="binance" and T0<=r["ts"]<T1 and (r["rtt"] is None or r["rtt"]>1000): nw.append(r["ts"])
con=collections.defaultdict(list)
for ex,root in (("binance","/data/collect/binance/data"),("gate","/data/collect/gate/data")):
    for f in glob.glob(root+"/events-*.jsonl"):
        for ln in open(f,errors="replace"):
            if '"ev":"connected"' not in ln and '"ev": "connected"' not in ln: continue
            i=ln.find('"ts_ms":'); ts=int(ln[i+8:].split("}")[0].split(",")[0])/1000
            if T0<=ts<T1: con[ex].append(ts)
s={int(k) for k,v in S["binance"].items() if v[1]>2000}
eps=[];cur=[]
for k in sorted(s):
    if cur and k-cur[-1]>6: eps.append(cur);cur=[]
    cur.append(k)
eps.append(cur)
for e in eps:
    a=T0+e[0]*10-60; b=T0+e[-1]*10+10
    n=[t for t in nw if a<=t<b]
    if not n: continue
    cb=[t for t in con["binance"] if a<=t<b]; cg=[t for t in con["gate"] if a<=t<b]
    f=lambda x: datetime.datetime.utcfromtimestamp(x).strftime("%H:%M:%S") if x else "-"
    print(f"episode {f(T0+e[0]*10)}: first SYN anomaly {f(n[0])} | first binance connect in window {f(cb[0] if cb else None)} (n={len(cb)}) | SYN anomalies before first binance connect: {sum(1 for t in n if not cb or t<cb[0])}/{len(n)} | gate connects n={len(cg)}")
