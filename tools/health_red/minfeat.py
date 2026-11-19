import json,subprocess,collections,datetime,statistics as st,sys
d=json.load(open("/tmp/v3.json"));s0=d["s0"];S=d["slot"];T0=s0;T1=s0+96*S
roots=json.load(open("/data/turbo-probe/page/roots.json"))
ser={}
for ex,root in roots.items():
    m={}
    for ln in open(root+"/series-minutes.jsonl",errors="replace"):
        try:v=json.loads(ln)
        except: continue
        if "t" in v and T0<=v["t"]<T1: m[v["t"]//60]=v
    ser[ex]=m
def sadf(f,args):
    out=subprocess.run(["sadf","-U","-d",f,"--"]+args,capture_output=True,text=True).stdout.splitlines()
    hdr=None;rows=[]
    for l in out:
        if l.startswith("#"): hdr=l[2:].split(";");continue
        rows.append(dict(zip(hdr,l.split(";"))))
    return rows
F=collections.defaultdict(lambda: collections.defaultdict(list))
def put(rows,mp,filt=None):
    for r in rows:
        if filt and not filt(r):continue
        ts=int(r["timestamp"])
        if not T0<=ts<T1: continue
        for k,s in mp.items():
            try:F[ts//60][k].append(float(r[s]))
            except:pass
for f in ["/var/log/sysstat/sa14","/var/log/sysstat/sa15"]:
    put(sadf(f,["-u"]),{"steal":"%steal","user":"%user","nice":"%nice","sys":"%system","iow":"%iowait","idle":"%idle"})
    put(sadf(f,["-q"]),{"runq":"runq-sz"})
    put(sadf(f,["-d"]),{"await":"await","util":"%util","wkB":"wkB/s"},lambda r:r["DEV"]=="vda")
    put(sadf(f,["-n","DEV"]),{"rxpck":"rxpck/s"},lambda r:r["IFACE"]=="eth0")
    put(sadf(f,["-n","ETCP"]),{"retr":"retrans/s"})
NW=collections.defaultdict(lambda: collections.defaultdict(lambda:[0,0]))
for fn in ["/data/netwatch/2026-11-14.jsonl","/data/netwatch/2026-11-15.jsonl"]:
    for ln in open(fn,errors="replace"):
        try:r=json.loads(ln)
        except: continue
        n=r.get("n");ts=r.get("ts")
        if n is None or ts is None or not T0<=ts<T1: continue
        rtt=r.get("rtt")
        lost= rtt is None or (n not in ("gateway","neutral") and rtt>700)
        c=NW[int(ts)//60][n];c[0]+=1;c[1]+=lost
out={}
for m in range(T0//60,T1//60):
    f=F.get(m,{})
    row={"m":m}
    for ex in roots:
        v=ser[ex].get(m)
        row[ex]=None if v is None else [v.get("lost_ms",0),sum(v.get("late",[0])),sum(v.get("to",[0,0]))]
    for k,l in f.items(): row[k]=[sum(l)/len(l),max(l)]
    row["nw"]={n:c for n,c in NW.get(m,{}).items()}
    out[m]=row
json.dump(out,open("/tmp/minfeat.json","w"))
print(len(out))
