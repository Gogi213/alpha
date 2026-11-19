"""bxfacts: classify the late (>2 s) records dumped by late.py, per shard kind:
  A_after_subscribe — the record arrived while a session was subscribing or <= 1 s after its `subscribed` (stale first frame of the topic);
  C_stall_window    — inside a stall window of the shard (gap / timeout events: from 1 s before the silence to 20-25 s after);
  B_isolated        — the rest: bursts of old states on one connection while the session was healthy (listed per shard and arrival ms).
  classify.py [late.json] [start_ms of the run]   ($BXFIX/ev/collect/bingx/data = copies of events-*.jsonl)
"""
import json,os,collections,datetime,bisect,sys
B=os.environ.get("BXFIX","/tmp/bxfix"); EVD=B+"/ev/collect/bingx/data"
LATE=sys.argv[1] if len(sys.argv)>1 else B+"/late.json"; START=int(sys.argv[2]) if len(sys.argv)>2 else 1794577010000
d=json.load(open(LATE))
# stall windows per shard from events (timeout / gap / ping_degraded / slow_pong), ms
ev=collections.defaultdict(list)
for sh in d:
    for ln in open(f"{EVD}/events-{sh}.jsonl",encoding="utf-8"):
        e=json.loads(ln)
        if e["ts_ms"]>=START: ev[sh].append(e)
def stall_windows(sh):
    w=[]
    for e in ev[sh]:
        if e["ev"]=="gap_end": w.append((e["gap_start_ms"]-1000, e["ts_ms"]+20000))
        elif e["ev"]=="gap": w.append((e["ts_ms"]-e["gap_ms"]-1000, e["ts_ms"]+25000))
        elif e["ev"]=="timeout": w.append((e["ts_ms"]-e["tcp"].get("last_data_recv_ms",10000)-2000, e["ts_ms"]+25000))
    return w
cls=collections.defaultdict(collections.Counter); delta=collections.defaultdict(collections.Counter)
bursts=collections.defaultdict(list)
for sh,v in d.items():
    conns=sorted(r[0] for r in v["c"] if r[1]=="connected")
    subs=sorted(r[0] for r in v["c"] if r[1]=="subscribed")
    sw=stall_windows(sh)
    kind=sh[:-2]
    for rx,k,sym,lag in v["late"]:
        i=bisect.bisect_right(conns,rx)-1
        j=bisect.bisect_right(subs,rx)-1
        c_since=(rx-conns[i])/1000 if i>=0 else None   # since last connect
        s_since=(rx-subs[j])/1000 if j>=0 else None    # since last subscribed
        # in subscribe phase: last connect is later than last subscribed -> still subscribing
        subscribing = i>=0 and (j<0 or conns[i]>subs[j])
        if subscribing or (s_since is not None and s_since<=1.0): c="A_after_subscribe(<=1s)"
        elif any(a<=rx<=b for a,b in sw): c="C_stall_window"
        elif s_since is not None and s_since<=5.0: c="A2_after_subscribe(1-5s)"
        else: c="B_isolated"
        cls[kind][c]+=1
        if c=="B_isolated": bursts[sh].append((rx,sym,lag))
print("late records >2 s by class (rx), start", START)
for kind in cls: print(kind, dict(cls[kind]), "total", sum(cls[kind].values()))
# B: bursts - same shard rx within 30 ms
print("\nB isolated records, grouped by shard and arrival ms (±30 ms):")
for sh,l in sorted(bursts.items()):
    l.sort(); groups=[]; 
    for r in l:
        if groups and r[0]-groups[-1][-1][0]<=30: groups[-1].append(r)
        else: groups.append([r])
    for g in groups:
        print(" ",sh, datetime.datetime.fromtimestamp(g[0][0]/1000,datetime.UTC).strftime("%H:%M:%S.%f")[:-3], "n=",len(g),"syms=",len({x[1] for x in g}),"lag min/max",min(x[2] for x in g),max(x[2] for x in g))
# B bursts merged into moments (groups of any shard within 1 s of each other)
allg = []
for sh, l in bursts.items():
    l.sort(); cur = []
    for r in l:
        if cur and r[0] - cur[-1][0] <= 30: cur.append(r)
        else:
            if cur: allg.append((cur[0][0], sh, len(cur), min(x[2] for x in cur), max(x[2] for x in cur)))
            cur = [r]
    if cur: allg.append((cur[0][0], sh, len(cur), min(x[2] for x in cur), max(x[2] for x in cur)))
allg.sort(); moments = []
for g in allg:
    if moments and g[0] - moments[-1][-1][0] <= 1000: moments[-1].append(g)
    else: moments.append([g])
multi = [m for m in moments if len({x[1] for x in m}) >= 2]
recs = sum(g[2] for g in allg)
hi = sum(g[2] for g in allg if g[4] >= 9000)
print(f"\nB: {len(allg)} bursts (shard x arrival ms) in {len(moments)} moments (1 s); moments on >= 2 shards: {len(multi)}; records {recs}; in bursts with max lag >= 9 s: {hi}")
