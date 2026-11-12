# Сверка множителя и «один актив» по цене (TK-509 r4). Вход: prices.json (fetch_prices.py), probe_all_pairs.csv. Выход: probe_all_pairs_mult.csv, recon_summary.md
# Правило: unit = price / множитель_из_имени; в кластере (≥2 биржи) цены актива совпадают ±5 % с точностью до степени 10; mult_eff = 10^round(log10(price) - ref).
# Вне кластера -> outlier (другой актив/устаревшая цена); нет цены на бирже (делист) -> no_price (не проверено).
import json,csv,re,math,collections,sys
P=json.load(open('prices.json'))
R=list(csv.DictReader(open('probe_all_pairs.csv',encoding='utf-8')))
MULT=re.compile(r'^(1000000|10000000|100000|10000|1000|1M|k)(?=[A-Z])')
MV={'1000000':1e6,'10000000':1e7,'100000':1e5,'10000':1e4,'1000':1e3,'1M':1e6,'k':1e3}
HL={}
for k,v in P['hyperliquid_futures'].items(): HL[k.upper() if ':' in k else k]=v; HL[k.upper()]=v
def price(ex,sym):
    if ex=='hyperliquid_futures': return HL.get(sym) or HL.get(sym.upper())
    return P[ex].get(sym)
def mname(ex,sym):
    b=sym
    if ex=='hyperliquid_futures': b=sym.rpartition(':')[2]
    else:
        for s in('-USDT-SWAP','_USDT','USDT','PERP'):
            if b.endswith(s): b=b[:-len(s)]; break
    if ex=='hyperliquid_futures' and not b.startswith('k'): b=b.upper()
    m=MULT.match(b)
    if m and len(b)>len(m.group(0))+1: return MV[m.group(0)]
    return 1.0
TOL=math.log10(1.05)
def d(a,b): return abs((a-b+0.5)%1-0.5)
by=collections.defaultdict(list)
for r in R:
    p=price(r['ex'],r['sym']); r['price']=p; r['mn']=mname(r['ex'],r['sym'])
    r['status']='no_price' if p is None or p<=0 else ''; r['mult_eff']=''
    if not r['status']: r['l']=math.log10(p/r['mn']); by[r['asset']].append(r)
for a,L in by.items():
    best=[];
    for s in L:
        g=[o for o in L if d(s['l'],o['l'])<=TOL]
        if len(g)>len(best): best=g
    if len(best)<2:
        for s in L: s['status']='unresolved'
        continue
    seed=best[0]['l']; ls=sorted(((o['l']-seed+0.5)%1)-0.5+seed for o in best); ref=ls[len(ls)//2]
    gid={id(o) for o in best}
    for s in L:
        if id(s) in gid:
            k=round(math.log10(s['price'])-ref); s['mult_eff']=10.0**k
            s['status']='ok' if abs(s['mult_eff']/s['mn']-1)<1e-9 else 'mult_diff'
        else: s['status']='outlier'
for r in R:
    if r['status']=='' : r['status']='no_price'
cols=['asset','class','ex','sym','dates_av_of_6','mb_per_day','kept','price','mn','mult_eff','status']
w=csv.writer(open('probe_all_pairs_mult.csv','w',newline='',encoding='utf-8')); w.writerow(cols[:-4]+['price','mult_name','mult_eff','status'])
for r in R: w.writerow([r[c] for c in cols[:-4]]+[r['price'] if r['price'] is not None else '',r['mn'],r['mult_eff'],r['status']])
kp=[r for r in R if r['kept']=='1']
print('Статусы пар (всего / kept=1):',dict(collections.Counter(r['status'] for r in R)),dict(collections.Counter(r['status'] for r in kp)))
byA=collections.defaultdict(list)
for r in kp: byA[r['asset']].append(r)
A=collections.Counter(); cls={}
for a,L in byA.items():
    cls[a]=L[0]['class']
    ver=[r for r in L if r['status'] in('ok','mult_diff')]; np_=[r for r in L if r['status']=='no_price']
    # А: >=2 проверенных; Б: проверенных+no_price >=2 (условно); иначе выпал
    t='A' if len(ver)>=2 else ('B' if len(ver)+len(np_)>=2 and len(ver)>=1 or len(np_)>=2 else 'X')
    A[t]+=1; 
    for r in L: r['tier']=t
print('Активов kept (912):',len(byA),'A (>=2 проверено):',A['A'],'B (условно, есть no_price):',A['B'],'X (выпали):',A['X'])
print('Класс×уровень:',dict(collections.Counter((cls[a],L[0]['tier']) for a,L in byA.items())))
md=[r for r in kp if r['status']=='mult_diff']
print('mult_diff пар:',len(md),'активов',len({r['asset'] for r in md}))
print('outlier пар:',sum(r['status']=='outlier' for r in kp),'активов',len({r['asset'] for r in kp if r['status']=='outlier'}))
# ГБ по уровням (36 сут, МБ/сутки -> ГБ), пары со статусами ok/mult_diff/no_price у активов A,B
g=collections.Counter()
for r in kp:
    if r['status'] in('ok','mult_diff','no_price') and r['tier'] in('A','B'): g[(r['tier'],r['class']=='крипта')]+=float(r['mb_per_day'])*36/1000
print('ГБ(36 сут) по (уровень, крипта?):',{k:round(v) for k,v in g.items()},'итого',round(sum(g.values())))
json.dump([[r['asset'],r['class'],r['ex'],r['sym'],r['status'],r['mn'],r['mult_eff'],r['tier']] for r in kp],open('recon_kept.json','w'),ensure_ascii=False)
