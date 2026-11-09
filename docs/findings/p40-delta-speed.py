#!/usr/bin/python3
# замер скорости CHFT: N контрактов × 24 ч одних суток, curl --parallel, сырьё удаляется. Ключ из /root/.chft.env
import csv,sys,os,subprocess,time,shutil,collections
P=int(sys.argv[1]); DAY=sys.argv[2]; PER=int(sys.argv[3]); D="/data/p40d-speed"
env=dict(l.strip().split("=",1) for l in open("/root/.chft.env") if "=" in l); K=env["CRYPTOHFTDATA_API_KEY"]
A="https://api.cryptohftdata.com"
by=collections.defaultdict(list)
for r in csv.DictReader(open("/root/p40t/contracts.csv")):
    if r["downloaded"]=="0" and r["ex"] in("okx_futures","bitget_futures","binance_futures","aster_futures","hyperliquid_futures"): by[r["ex"]].append(r)
rel=[]
for ex,l in by.items():
    l=sorted(l,key=lambda r:-float(r["gb"]))[len(l)//3::max(1,len(l)//PER)][:PER]
    for r in l:
        for h in range(24): rel.append("%s/%s/%02d/%s_orderbook.parquet"%(ex,DAY,h,r["sym"]))
shutil.rmtree(D,ignore_errors=True); os.makedirs(D)
c1=D+"/c1.cfg"
open(c1,"w").write("header = \"X-API-Key: %s\"\n"%K+"".join("output = /dev/null\nurl = \"%s/v1/download?file=%s\"\n"%(A,x) for x in rel)); os.chmod(c1,0o600)
t0=time.time()
pr=subprocess.run(["curl","-s","-Z","-m","120","--parallel-max",str(P),"--http2","-w","%{url_effective}\t%{http_code}\t%{redirect_url}\n","-K",c1],capture_output=True,text=True)
t1=time.time(); links={}; n404=0
for l in pr.stdout.splitlines():
    p=l.split("\t")
    if len(p)==3 and "file=" in p[0]:
        if p[1]=="302" and p[2]: links[p[0].split("file=",1)[1]]=p[2]
        elif p[1]=="404": n404+=1
os.remove(c1)
c2=D+"/c2.cfg"
open(c2,"w").write("".join("output = \"%s/%d.p\"\nurl = \"%s\"\n"%(D,i,u) for i,u in enumerate(links.values())))
t2=time.time()
pr=subprocess.run(["curl","-s","-Z","-m","600","--parallel-max",str(P),"--http2","-w","%{exitcode}\t%{http_code}\t%{size_download}\n","-K",c2],capture_output=True,text=True)
t3=time.time()
sz=0;ok=0;bad=0
for l in pr.stdout.splitlines():
    p=l.split("\t")
    if len(p)==3 and p[0]=="0" and p[1]=="200": ok+=1; sz+=int(p[2])
    else: bad+=1
print("P=%d reqs=%d links=%d 404=%d phase1=%.1fs; dl ok=%d bad=%d %.0f MB in %.1fs = %.1f MB/s"%(P,len(rel),len(links),n404,t1-t0,ok,bad,sz/1e6,t3-t2,sz/1e6/(t3-t2)))
shutil.rmtree(D,ignore_errors=True)
