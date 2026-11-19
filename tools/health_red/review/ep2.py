import json,collections,subprocess,datetime,glob,statistics as st
d=json.load(open("/tmp/v3.json"));T0=d["s0"];T1=T0+96*900
S=json.load(open("/tmp/stall.json"))
nw=collections.defaultdict(list)
for day in ("2026-11-14","2026-11-15"):
    for ln in open(f"/data/netwatch/{day}.jsonl"):
        try:r=json.loads(ln)
        except: continue
        if T0<=r["ts"]<T1 and r["n"]=="binance": nw[int(r["ts"])].append(r["rtt"])
# sar eth0 per 10 s
rx={}
for sa in ("sa14","sa15"):
    out=subprocess.run(["sadf","-d","/var/log/sysstat/"+sa,"--","-n","DEV"],capture_output=True,text=True).stdout
    for ln in out.splitlines():
        p=ln.split(";")
        if len(p)<8 or p[3]!="eth0": continue
        t=int(datetime.datetime.strptime(p[2],"%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=datetime.timezone.utc).timestamp())
        rx[t]=(float(p[4]),float(p[6]),float(p[7]))
rxs=sorted(rx)
def rxin(a,b): return [rx[t] for t in rxs if a<=t<b]
allrx=[v[1] for v in rx.values() if T0<=0 or True]
med=st.median(v[1] for t,v in rx.items() if T0<=t<T1); medp=st.median(v[0] for t,v in rx.items() if T0<=t<T1)
print(f"day eth0 rx median {med:.0f} kB/s, {medp:.0f} pkt/s; p99 {sorted(v[1] for t,v in rx.items() if T0<=t<T1)[int(.99*8000)]:.0f} kB/s")
def episodes(s):
    out=[];cur=[]
    for k in sorted(s):
        if cur and k-cur[-1]>6: out.append(cur);cur=[]
        cur.append(k)
    if cur: out.append(cur)
    return out
anom={t for t,v in nw.items() if any(x is None or x>1000 for x in v)}
print("netwatch binance anomalies (timeout or >1s) in day:",len(anom))
for ex in ("binance","gate"):
    s={int(k) for k,v in S[ex].items() if v[1]>2000}
    eps=episodes(s); covered=set()
    print("==",ex)
    for e in eps:
        a=T0+e[0]*10-30; b=T0+e[-1]*10+10
        an=sorted(t for t in anom if a<=t<b); covered|=set(an)
        r=rxin(a,b); rmax=max((v[1] for v in r),default=0)
        print(f" {datetime.datetime.utcfromtimestamp(T0+e[0]*10):%H:%M:%S} bins={len(e):3d} lagmax={max(S[ex][str(k)][1] for k in e)/1000:5.1f}s nw_anom={len(an):2d} first_anom_vs_start={(an[0]-(T0+e[0]*10)) if an else '-'} rx_max={rmax:.0f}kB/s rx_mean={st.mean(v[1] for v in r) if r else 0:.0f}")
    print(" anomalies inside",ex,"episodes:",len(covered),"of",len(anom))
