import os,glob,csv,collections,re,sys
A='/home/deck/alpha'; M=['jan','feb','mar','apr','may','jun','jul','aug']
pool={}
for r in csv.DictReader(open('/root/pool-v171.csv')): pool[(r['sym'],r['day'])]=r['in_pool']
EX=set("HYPEUSDT NEARUSDT XLMUSDT LITUSDT PENDLEUSDT TRXUSDT PUMPFUNUSDT ATOMUSDT JUPUSDT WIFUSDT DRAMUSDT".split())
dropped=collections.defaultdict(str)
for m in M:
  for pre in ('dropped-','dropped11-'):
    p=f'{A}/tk031/{pre}{m}.txt'
    if os.path.exists(p):
      for l in open(p):
        w=l.split()
        if len(w)>=2: dropped[(m,w[0],w[1])]=pre
HL=open('/dev/null','w'); RC=open('/home/deck/alpha/tk031/dropped-reasons.csv','w'); RC.write('month,symbol,day,reason,in_pool_v171,in_dropped_txt'+chr(10)); res=collections.Counter(); ex=collections.defaultdict(list)
for i,m in enumerate(M,1):
  S=f'/data/alpha/epochs/e-{m}/root'
  bl=set()
  for f in os.listdir(S):
    g=re.match(r'^(.+)-(2026-\d\d-\d\d)\.binlog$',f)
    if g: bl.add((g[1],g[2]))
  mg=f'{A}/merged/e-{m}/b5/p05-a1'
  have=set()
  for day in os.listdir(mg):
    fp=f'{mg}/{day}/p05-a45-u250k/forms.csv'
    if os.path.exists(fp):
      for l in open(fp):
        if l[0]!='#' and not l.startswith('symbol,'): have.add((l.split(',',1)[0],day))
  sig=lambda s: os.path.exists(f'{A}/epochs/e-{m}/study/sigma240/sigma-{s}.csv')
  for (s,d) in sorted(bl):
    if (s,d) in have: res[(m,'ok')]+=1; continue
    # why missing
    mm,dd=d[5:7],d[8:10]; key=f'{mm}-{dd}'
    ap65=os.path.exists(f'{A}/epochs/e-{m}/study/approaches/D20/{d}/approaches-{s}.csv') or bool(glob.glob(f'{A}/epochs/e-{m}/study/approaches/D20/{d}/*{s}*'))
    ap11=os.path.exists(f'/root/tk035ap/out/{d}/approaches-{s}.csv')
    r=[]
    if not sig(s): r.append('no-sigma')
    if not (ap65 or ap11): r.append('no-approaches')
    r.append('inpool=%s'%pool.get((s,d),'absent'))
    r.append('dropped' if (m,key,s) in dropped else 'NOT-in-dropped')
    if pool.get((s,d)): HL.write(m+' '+s+' '+d+chr(10))
    RC.write(','.join([m,s,d,'+'.join(x for x in r if x.startswith('no-')) or ('not-in-pool' if not pool.get((s,d)) else 'tool-skip'),'yes' if pool.get((s,d)) else 'no','yes' if (m,key,s) in dropped else 'no'])+chr(10))
    k=(m,' '.join(r)); res[k]+=1; ex[k].append(f'{s}@{d}')
HL.close()
for k,v in sorted(res.items(),key=str): print(k,v, ex.get(k,[])[:2] if k[1]!='ok' else '')
RC.close()
