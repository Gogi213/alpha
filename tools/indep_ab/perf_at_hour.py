"""perf sched_process_exec/fork per second around an hour boundary + top commands; maps perf time (uptime clock) to epoch via psi.log pairs.
Usage: perf_at_hour.py <perf script text> <epoch hour> [before after]"""
import sys,re,collections,datetime
txt,H=sys.argv[1],int(sys.argv[2]); before=int(sys.argv[3]) if len(sys.argv)>3 else 5; after=int(sys.argv[4]) if len(sys.argv)>4 else 12
# uptime<->epoch from psi.log (epoch, uptime)
off=[]
for l in open('/data/indep-ab/host/psi.log'):
    p=l.split()
    try: e,u=float(p[0]),float(p[1])
    except: continue
    if abs(e-H)<600: off.append(e-u)
offs=sorted(off)[len(off)//2]
ev=[]
for l in open(txt):
    m=re.match(r'\s*(.+?)\s+(\d+)\s+([\d.]+): sched:(sched_process_\w+): (.*)',l)
    if not m: continue
    comm,pid,t,kind,rest=m.groups()
    ev.append((float(t)+offs,kind,comm,rest))
def win(a,b): return [e for e in ev if H+a<=e[0]<H+b]
base=win(-600,-30); nb=570
print('events total in file',len(ev),'| baseline -600..-30 s: exec %.1f/s fork %.1f/s'%(sum(1 for e in base if e[1]=='sched_process_exec')/nb,sum(1 for e in base if e[1]=='sched_process_fork')/nb))
for s in range(-before,after):
    w=win(s,s+1); ex=[e for e in w if e[1]=='sched_process_exec']; fk=[e for e in w if e[1]=='sched_process_fork']
    names=collections.Counter(re.sub(r'.*filename=(\S+).*',r'\1',e[3]) for e in ex)
    print('%+3d s | exec %3d fork %3d | %s'%(s,len(ex),len(fk),', '.join('%s x%d'%(k.split('/')[-1],v) for k,v in names.most_common(6))))
# every exec in [0,8) not seen in baseline (new command names)
bn={re.sub(r'.*filename=(\S+).*',r'\1',e[3]) for e in base if e[1]=='sched_process_exec'}
new=collections.Counter(re.sub(r'.*filename=(\S+).*',r'\1',e[3]) for e in win(-2,15) if e[1]=='sched_process_exec')
print('exec in -2..+15 s with names NOT in baseline -600..-30:',{k:v for k,v in new.items() if k not in bn})
