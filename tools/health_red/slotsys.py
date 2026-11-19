import json,subprocess,datetime,collections,statistics as st
d=json.load(open("/tmp/v3.json"))
s0=d["s0"];S=d["slot"];N=96
def sadf(f,args):
    out=subprocess.run(["sadf","-U","-d",f,"--"]+args,capture_output=True,text=True).stdout.splitlines()
    hdr=None;rows=[]
    for l in out:
        if l.startswith("#"): hdr=l[2:].split(";");continue
        p=l.split(";");rows.append(dict(zip(hdr,p)))
    return rows
files=["/var/log/sysstat/sa14","/var/log/sysstat/sa15"]
agg=collections.defaultdict(lambda: collections.defaultdict(list))
def put(rows,mapping,filt=None):
    for r in rows:
        if filt and not filt(r): continue
        ts=int(r["timestamp"]);i=(ts-s0)//S
        if 0<=i<N:
            for k,src in mapping.items():
                try: agg[i][k].append(float(r[src]))
                except: pass
for f in files:
    put(sadf(f,["-u"]),{"user":"%user","nice":"%nice","sys":"%system","iow":"%iowait","steal":"%steal"})
    put(sadf(f,["-q"]),{"runq":"runq-sz","blk":"blocked"})
    put(sadf(f,["-d"]),{"await":"await","util":"%util","wkB":"wkB/s","rkB":"rkB/s","tps":"tps"},lambda r:r["DEV"]=="vda")
    put(sadf(f,["-n","DEV"]),{"rxpck":"rxpck/s","txpck":"txpck/s","rxkB":"rxkB/s","txkB":"txkB/s"},lambda r:r["IFACE"]=="eth0")
    put(sadf(f,["-n","ETCP"]),{"retr":"retrans/s","isegerr":"isegerr/s","orsts":"orsts/s"})
    put(sadf(f,["-n","TCP"]),{"active":"active/s"})
red={r["id"]:[b[0] for b in r["bars"]] for r in d["rows"]}
lost={r["id"]:[b[1] for b in r["bars"]] for r in d["rows"]}
late={r["id"]:[b[2] for b in r["bars"]] for r in d["rows"]}
def m(l): return sum(l)/len(l) if l else float("nan")
def mx(l): return max(l) if l else float("nan")
print("slot GMT+4 UTC | steal avg/max | cpu(u+n+s) avg | iow max | runq avg/max | await avg/max | util max | wMB/s | rMB/s | rxpck | retr/s max | red: bn by bb ok bx gt (lost s bn/bb)")
for i in range(N):
    a=agg[i]
    if not a: continue
    t4=datetime.datetime.utcfromtimestamp(s0+i*S+4*3600).strftime("%H:%M")
    tu=datetime.datetime.utcfromtimestamp(s0+i*S).strftime("%H:%M")
    cpu=[u+n+s for u,n,s in zip(a["user"],a["nice"],a["sys"])]
    flags="".join(("R" if red[e][i]==1 else ("g" if red[e][i]==2 else ".")) for e in ["binance","bybit","okx","bingx","gate"])
    print(f"{i:2d} {t4} {tu} | st {m(a['steal']):5.1f}/{mx(a['steal']):5.1f} | cpu {m(cpu):5.1f} | iow {mx(a['iow']):5.1f} | rq {m(a['runq']):4.1f}/{mx(a['runq']):4.0f} | aw {m(a['await']):5.1f}/{mx(a['await']):6.1f} | ut {mx(a['util']):5.1f} | w {m(a['wkB'])/1024:6.1f} | r {m(a['rkB'])/1024:6.1f} | rx {m(a['rxpck']):7.0f} | rt {mx(a['retr']):5.1f} | {flags} {lost['binance'][i]/1000:6.1f} {lost['bybit'][i]/1000:5.1f}")
json.dump({i:{k:v for k,v in a.items()} for i,a in agg.items()},open("/tmp/slotsys.json","w"))
