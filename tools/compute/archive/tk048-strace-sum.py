import re,sys,collections
rd=re.compile(r'^\d+\s+(read|pread64)\((\d+)<([^>]*)>.*\)\s+=\s+(\d+)$')
op=re.compile(r'^\d+\s+openat\(.*\)\s+=\s+\d+<([^>]*)>')
mm=re.compile(r'^\d+\s+mmap\(\S+\s+(\d+),.*MAP_\w+,\s+\d+<([^>]*)>,.*=\s+0x')
def disk(p):
    if p.startswith('/dev/shm'): return 'shm'
    if p.startswith('/alpha-sda'): return 'sda'
    if p.startswith('/data'): return 'sdb'
    return 'root(sda)'
def cls(p):
    b=p.rsplit('/',1)[-1]
    if '.binlog.events' in b: return 'binlog.events'
    if '.binlog' in b: return 'binlog'
    if b.endswith('.abin'): return 'abin(approaches/touches)'
    if re.match(r'(approaches|touches)-.*\.csv$',b): return b.split('-')[0]+'.csv'
    if 'sigma' in p: return 'sigma'
    if '/regime/' in p: return 'regime'
    if 'carry' in p: return 'ev_carry'
    if b in('verdict.csv','instruments.csv'): return b
    if p.startswith('/proc') or p.startswith('/sys'): return 'proc/sys'
    if re.search(r'\.(so|so\.\d+.*)$',b) or p.startswith(('/usr/lib','/lib','/etc')): return 'libs/etc'
    if '/b5/' in p or '/g/' in p: return 'output(b5/g)'
    return 'other'
R=collections.defaultdict(lambda:[0,0,0,0]); files=collections.defaultdict(set); other=collections.Counter()
for l in open(sys.argv[1],errors='replace'):
    m=rd.match(l)
    if m:
        p=m.group(3);k=(cls(p),disk(p));R[k][1]+=int(m.group(4));R[k][2]+=1;files[k].add(p)
        if k[0]=='other': other[p]+=int(m.group(4))
        continue
    m=op.match(l)
    if m: p=m.group(1);R[(cls(p),disk(p))][0]+=1; continue
    m=mm.match(l)
    if m: p=m.group(2);R[(cls(p),disk(p))][3]+=int(m.group(1))
print("класс | диск | открытий | read МБ | read вызовов | ср.размер КБ | mmap МБ (верхняя оценка) | файлов с чтением")
for k,v in sorted(R.items(),key=lambda x:-x[1][1]):
    print(f"{k[0]} | {k[1]} | {v[0]} | {v[1]/1e6:.1f} | {v[2]} | {v[1]/max(v[2],1)/1024:.0f} | {v[3]/1e6:.1f} | {len(files[k])}")
tot=collections.Counter()
for k,v in R.items(): tot[k[1]]+=v[1]
print("ИТОГО read МБ по дискам:",{a:round(b/1e6,1) for a,b in tot.items()})
print("other топ-8 путей по байтам:")
for p,b in other.most_common(8): print(f"  {b/1e6:.1f} МБ {p}")
