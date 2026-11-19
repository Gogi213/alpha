import subprocess,re,collections
out=subprocess.run(["ss","-tinpH","state","established"],capture_output=True,text=True).stdout.splitlines()
socks=[];cur=None
for ln in out:
    if not ln.startswith((" ","\t")):
        if cur: socks.append(cur)
        cur={"head":ln,"info":""}
    else: cur["info"]+=" "+ln.strip()
if cur: socks.append(cur)
P={"alpha-collect-a":"binance","alpha-collect-g":"gate","alpha-collect-o":"okx","alpha-collect-b":"bingx/bybit"}
by=collections.defaultdict(lambda:collections.Counter())
sock=[]
for s in socks:
    m=re.search(r'users:\(\("([^"]+)"',s["head"]);
    if not m or m.group(1) not in P: continue
    f=s["head"].split(); peer=f[3]; ip=peer.rsplit(":",1)[0]
    ex=P[m.group(1)]
    g=lambda k:int(re.search(r'\b'+k+r':(\d+)',s["info"]).group(1)) if re.search(r'\b'+k+r':(\d+)',s["info"]) else 0
    rtt=re.search(r'\brtt:([\d.]+)/',s["info"]); 
    sock.append((ex,ip,g("segs_in"),g("rcv_ooopack"),float(rtt.group(1)) if rtt else 0))
for ex,ip,si,oo,rtt in sock:
    c=by[(ex,ip)];c["n"]+=1;c["segs"]+=si;c["ooo"]+=oo;c["rtt"]+=rtt
rows=sorted(by.items(),key=lambda kv:(kv[0][0],-kv[1]["ooo"]/max(1,kv[1]["segs"])))
for (ex,ip),c in rows:
    print(f"{ex:12s} {ip:18s} socks={c['n']:3d} segs_in={c['segs']:10d} ooo%={100*c['ooo']/max(1,c['segs']):.3f} rtt_avg={c['rtt']/c['n']:.0f}ms")
# per-socket ooo% distribution
import statistics as st
for ex in ("binance","gate","okx","bingx/bybit"):
    v=sorted(100*o/max(1,s) for e,i,s,o,r in sock if e==ex and s>100000)
    if v: print(ex,"per-socket ooo% (sockets with >100k segs):n=",len(v),"min",round(v[0],3),"median",round(v[len(v)//2],3),"p90",round(v[int(len(v)*.9)],3),"max",round(v[-1],3))
