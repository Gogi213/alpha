import subprocess,collections,statistics,sys,re,os
rows=[]
for f in sorted(os.listdir('/var/log/sysstat')):
    if not re.match(r'sa\d\d$',f): continue
    out=subprocess.run(['sar','-f','/var/log/sysstat/'+f,'-u'],capture_output=True,text=True,env={'LC_ALL':'C','S_TIME_FORMAT':'ISO'}).stdout
    day=None
    for l in out.splitlines():
        p=l.split()
        if len(p)>=8 and re.match(r'\d\d:\d\d:\d\d$',p[0]) and p[1]=='all':
            rows.append((f,p[0],float(p[2]),float(p[3]),float(p[4]),float(p[5]),float(p[6]),float(p[7])))
# columns: f,time,user,nice,system,iowait,steal,idle
def sec(t): h,m,s=map(int,t.split(':')); return h*3600+m*60+s
byhour=collections.defaultdict(dict)
for f,t,u,n,s_,io,st,idl in rows:
    byhour[(f,sec(t)//3600*3600)][sec(t)%3600]=st
allsteal=[r[6] for r in rows]
print('buckets',len(rows),'steal median %.2f p99 %.2f max %.2f'%(statistics.median(allsteal),sorted(allsteal)[int(len(allsteal)*0.99)],max(allsteal)))
# bucket label t covers (t-10,t]; the one containing :00:00 is labelled 00:05..00:10 region; collect steal at labels [-5..+25) rel. hour start
rel=collections.defaultdict(list)
for (f,h),d in byhour.items():
    for t,v in d.items(): rel[t//10*10].append(v)
# NOTE: second-of-hour distribution of steal>10%
hi=collections.Counter(); tot=collections.Counter()
for f,t,u,n,s_,io,st,idl in rows:
    k=sec(t)%3600
    tot[k//60]+=1
    if st>8: hi[k//60]+=1
print('buckets with steal>8% by minute-of-hour:',sorted(hi.items()))
print('(of ~%d buckets per minute-of-hour)'%max(tot.values()))
for (f,h),d in sorted(byhour.items())[:0]: pass
print('steal>8% list (file, time):')
for f,t,u,n,s_,io,st,idl in rows:
    if st>8: print(' ',f,t,'steal',st,'idle',idl)
