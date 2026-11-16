import json,glob,sys,collections,datetime
t0=int(sys.argv[1]);t1=int(sys.argv[2])
def load(pattern,step=10):
    agg=collections.defaultdict(lambda:[0,0.0,0.0,0.0])
    for f in glob.glob(pattern):
        for ln in open(f,errors="replace"):
            i=ln.find('"ts_ms":')
            if i<0: continue
            try: ts=int(ln[i+8:].split("}")[0].split(",")[0])//1000
            except: continue
            if not t0<=ts<t1: continue
            try:r=json.loads(ln)
            except: continue
            k=(ts-t0)//step
            a=agg[k];a[0]+=r.get("msgs",0) or 0;a[1]=max(a[1],r.get("lag_p99_ms") or 0);a[2]=max(a[2],r.get("queue_lag_p99_ms") or 0);a[3]=max(a[3],r.get("busy_max_us") or 0)
    return agg
for ex,sr in (("binance","/data/collect/binance/data"),("gate","/data/collect/gate/data")):
    A=load(sr+"/stats-*.jsonl"); B=load(f"/tmp/sofia/{ex}/stats-*.jsonl")
    common=sorted(set(A)&set(B))
    TH=2000
    sa={k for k in common if A[k][1]>TH}; sb={k for k in common if B[k][1]>TH}
    near=lambda s,k: any((k+x) in s for x in (-1,0,1))
    print(f"{ex}: window {datetime.datetime.utcfromtimestamp(t0):%H:%M}-{datetime.datetime.utcfromtimestamp(t1):%H:%M} UTC bins={len(common)} | srv stall bins={len(sa)} | sofia stall bins={len(sb)} | both={len(sa&sb)}")
    print(f"   srv stall bins with a Sofia stall within +-10 s: {sum(1 for k in sa if near(sb,k))}/{len(sa)}; Sofia stall bins with srv stall within +-10 s: {sum(1 for k in sb if near(sa,k))}/{len(sb)}")
    # episodes: consecutive stall bins merged
    def episodes(s):
        out=[];cur=[]
        for k in sorted(s):
            if cur and k-cur[-1]>6: out.append(cur);cur=[]
            cur.append(k)
        if cur: out.append(cur)
        return out
    for name,s,X in (("srv",sa,A),("sofia",sb,B)):
        eps=episodes(s)
        print("   ",name,"episodes:",[(datetime.datetime.utcfromtimestamp(t0+e[0]*10).strftime("%H:%M:%S"),len(e)*10,int(max(X[k][1] for k in e))) for e in eps][:12])
