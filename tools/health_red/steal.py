import json,subprocess,datetime,collections
d=json.load(open("/tmp/v3.json"));T0=d["s0"];T1=T0+96*900
S=json.load(open("/tmp/stall.json"))
bins={e:{int(k):v for k,v in S[e].items()} for e in S}
def sadf(f):
    out=subprocess.run(["sadf","-U","-d",f,"--","-u"],capture_output=True,text=True).stdout.splitlines()
    return [dict(zip(out[0][2:].split(";"),l.split(";"))) for l in out[1:] if not l.startswith("#")]
rows=[]
for f in ["/var/log/sysstat/sa14","/var/log/sysstat/sa15"]:
    for r in sadf(f):
        ts=int(r["timestamp"])
        if T0<=ts<T1: rows.append((ts,float(r["%steal"]),float(r["%user"])+float(r["%nice"])+float(r["%system"]),float(r["interval"])))
print("samples",len(rows))
import statistics as st
stl=[r[1] for r in rows]
print("steal %: p50",st.median(stl),"p90",sorted(stl)[int(len(stl)*.9)],"p99",sorted(stl)[int(len(stl)*.99)],"max",max(stl))
for th in (10,15,20,30):
    print(f"samples with steal>={th}%:",sum(1 for x in stl if x>=th))
def stalled(b):
    ex=[e for e in bins if b in bins[e] and bins[e][b][1]>2000]
    return ex
print("samples steal>=20 %: time UTC | steal | cpu | exchanges with lag_p99>2s in same/next bin")
hr=collections.Counter();minute=collections.Counter()
for ts,s,c,iv in rows:
    if s>=20:
        b=(ts-T0-5)//10
        ex=set(stalled(b))|set(stalled(b+1))
        t=datetime.datetime.utcfromtimestamp(ts)
        hr[t.hour]+=1; minute[t.minute]+=1
        print(" ",t.strftime("%H:%M:%S"),f"{s:5.1f}",f"{c:5.1f}",sorted(ex))
print("by minute-of-hour (top):",minute.most_common(6))
# converse: every exchange stall bin -> steal in that bin
for e in ("binance","gate"):
    ss=[b for b in bins[e] if bins[e][b][1]>2000]
    vals=[]
    for b in ss:
        near=[r[1] for r in rows if abs((r[0]-5-T0)//10-b)<=1]
        vals.append(max(near) if near else None)
    vv=[v for v in vals if v is not None]
    print(e,"stall bins",len(ss),"max steal in bin±1: median",st.median(vv),"p90",sorted(vv)[int(len(vv)*.9)],"max",max(vv),"| bins with steal>=20:",sum(1 for v in vv if v>=20))
