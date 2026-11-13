"""bxfacts: netwatch samples (1 Hz TCP-connect RTT to exchange edges, ICMP to gateway and 8.8.8.8) in a window; missing or > 500 ms samples ($BXFIX/sar/nw.jsonl).
  netwatch.py <t0 epoch s> <from offset s> <to offset s>
"""
import json,os,sys,collections,datetime
B=os.environ.get("BXFIX","/tmp/bxfix")
t0=float(sys.argv[1]); a=float(sys.argv[2]); b=float(sys.argv[3])
rows=collections.defaultdict(list)
for ln in open(B+"/sar/nw.jsonl"):
    if ln.startswith('{"meta"'): continue
    r=json.loads(ln)
    if t0+a<=r["ts"]<=t0+b: rows[r["n"]].append((datetime.datetime.fromtimestamp(r["ts"],datetime.UTC).strftime("%H:%M:%S"), r.get("rtt")))
for n,v in sorted(rows.items()):
    slow=[x for x in v if x[1] is None or x[1]>500]
    mx=max((x[1] for x in v if x[1] is not None),default=None)
    print(n,"samples",len(v),"missing/>500ms:",slow,"max",mx)
