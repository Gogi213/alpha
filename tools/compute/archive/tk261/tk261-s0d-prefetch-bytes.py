import os, csv, subprocess, glob
E='/data/tk046/jan/home/alpha/epochs/e-jan'
V='/data/tk044/final3/verdict.csv'
rows=list(csv.DictReader(open(V)))
bydy={}
for r in rows: bydy.setdefault(r['day'],set()).add(r['sym'])
def sz(p):
    try: return os.stat(p).st_size
    except OSError: return 0
tot_pf=0; tot_head=0; tot_small=0; allbin=0; nbin=0
sample=[]
for i in range(1,16):
    d='2026-01-%02d'%i
    rd=os.path.realpath(E+'/study/root-'+d)
    fs=sorted(os.listdir(rd)) if os.path.isdir(rd) else []
    bins=[f for f in fs if '.binlog' in f]; oth=[f for f in fs if '.binlog' not in f]
    allsz=sum(sz(os.path.realpath(rd+'/'+f)) for f in bins)
    pf=0; nv=0
    for s in bydy.get(d,()):
        for f in bins:
            if f.startswith(s+'-'+d):
                pf+=sz(os.path.realpath(rd+'/'+f)); nv+=1
                if len(sample)<28 and i in (5,6,7,8): sample.append(os.path.realpath(rd+'/'+f))
    small=sum(sz(os.path.realpath(rd+'/'+f)) for f in oth)
    appr=E+'/study/approaches/D20/'+d
    ap=0; na=0
    if os.path.isdir(appr):
        for f in os.listdir(appr): ap+=sz(os.path.realpath(appr+'/'+f)); na+=1
    print(d,'binlogs_all',len(bins),round(allsz/1e9,2),'GB','verdict_syms',nv,round(pf/1e9,2),'GB','other_in_root',len(oth),round(small/1e6,1),'MB','approaches',na,round(ap/1e6,1),'MB')
    if i<=10: tot_pf+=pf
    if i<=5: tot_head+=len(bins)*65536; tot_small+=small+ap
print('prefetch(10 суток) cat целиком, ГБ',round(tot_pf/1e9,2))
print('prewarm(5 суток): head-c 64К ×файлов, МБ',round(tot_head/1e6,1),' + мелкие целиком, МБ',round(tot_small/1e6,1))
print('== filefrag sample',len(sample))
out=subprocess.run(['filefrag']+sample,capture_output=True,text=True).stdout
for l in out.splitlines(): print(l.replace(E,'E')[-110:])
