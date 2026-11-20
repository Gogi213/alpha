# Сводка оборота. Запуск: python3 -I summarize.py contracts.csv full out_dir  -> contract_median.csv, summary.md
import csv,sys,collections,statistics as st
C=list(csv.DictReader(open(sys.argv[1],encoding='utf-8'))); full=sys.argv[2]; out=sys.argv[3]
d=collections.defaultdict(list)
for r in csv.DictReader(open(full+'/turnover.csv',encoding='utf-8')): d[(r['ex'],r['sym'])].append(float(r['turnover_usd']))
err={(r['ex'],r['sym']):r['err'] for r in csv.DictReader(open(full+'/status.csv',encoding='utf-8'))}
EX=['binance_futures','bybit_futures','okx_futures','bitget_futures','gate_futures','aster_futures','hyperliquid_futures']
med={}
w=csv.writer(open(out+'/contract_median.csv','w',newline='',encoding='utf-8')); w.writerow(['asset','class','ex','sym','in_set','downloaded','gb','n_days','median_usd','min_usd','err'])
for r in C:
    k=(r['ex'],r['sym']); v=d.get(k,[]); m=st.median(v) if v else None; med[k]=m
    w.writerow([r['asset'],r['class'],r['ex'],r['sym'],r['in_set'],r['downloaded'],r['gb'],len(v),'' if m is None else round(m,0),round(min(v),0) if v else '',err.get(k,'')])
L=[]
def M(x): return '-' if x is None else f'{x/1e6:.2f}'
V=['BRETT','TA','LAB','WCT','H','LPT','BROCCOLI','ONT','APE','ZHIPU','MINIMAX','TENCENT','XIAOMI','SHEIN','BTC','ETH']
L.append('## 1. Видео-набор: медиана дневного оборота, млн USD (17.09–22.10, 36 сут)\n\n| актив | '+' | '.join(e.replace('_futures','') for e in EX)+' |\n|'+'---|'*(len(EX)+1))
by={(r['asset'],r['ex']):r for r in C}
for a in V: L.append(f'| {a} | '+' | '.join((M(med.get((e,by[(a,e)]['sym']))) if (a,e) in by else '-') for e in EX)+' |')
TH=[('<100 тыс',0,1e5),('100 тыс–1 млн',1e5,1e6),('1–10 млн',1e6,1e7),('≥10 млн',1e7,1e99)]
def bucket(m):
    for n,a,b in TH:
        if m is not None and a<=m<b: return n
    return 'нет данных'
NAMES=[t[0] for t in TH]+['нет данных']
# 2. активы (in_set): оборот актива = макс по биржам медианы контракта; и сумма
A=collections.defaultdict(dict); cls={}
for r in C:
    if r['in_set']=='1': A[r['asset']][r['ex']]=med[(r['ex'],r['sym'])]; cls[r['asset']]=r['class']
L.append(f'\n## 2. Активы вселенной ({len(A)}): число активов по корзинам\nОборот актива: (а) максимум по биржам медианы контракта, (б) сумма медиан по биржам.\n')
for nm,f in [('(а) макс по биржам',lambda v:max([x for x in v if x is not None],default=None)),('(б) сумма по биржам',lambda v:sum(x for x in v if x is not None) if any(x is not None for x in v) else None)]:
    L.append(f'\n**{nm}**\n\n| класс | активов | '+' | '.join(NAMES)+' |\n|'+'---|'*(len(NAMES)+2))
    T=collections.defaultdict(collections.Counter)
    for a,v in A.items(): T[cls[a]][bucket(f(list(v.values())))]+=1; T['ВСЕГО'][bucket(f(list(v.values())))]+=1
    for c in sorted(T,key=lambda c:(c=='ВСЕГО',c)): L.append(f'| {c} | {sum(T[c].values())} | '+' | '.join(str(T[c][n]) for n in NAMES)+' |')
# 3. контракты: скачанные / не скачанные, с ГБ
L.append('\n## 3. Контракты (in_set, 4373): корзины по медиане контракта; не скачано — с ГБ (36 сут)\n\n| группа | биржа/класс | контрактов | '+' | '.join(NAMES)+' | ГБ по корзинам |\n|'+'---|'*(len(NAMES)+4))
for grp,flt in [('не скачано',lambda r:r['downloaded']=='0'),('скачано',lambda r:r['downloaded']=='1')]:
    for keyf,lab in [(lambda r:'ВСЕГО','все'),(lambda r:'крипта' if r['class']=='крипта' else 'не-крипта','класс'),(lambda r:r['ex'],'биржа')]:
        T=collections.defaultdict(collections.Counter); G=collections.defaultdict(lambda:collections.defaultdict(float))
        for r in C:
            if r['in_set']!='1' or not flt(r): continue
            b=bucket(med[(r['ex'],r['sym'])]); k=keyf(r); T[k][b]+=1; G[k][b]+=float(r['gb'])
        for k in sorted(T): L.append(f'| {grp} | {k} | {sum(T[k].values())} | '+' | '.join(str(T[k][n]) for n in NAMES)+' | '+' / '.join(f'{G[k][n]:.0f}' for n in NAMES)+' |')
# 4. нет истории
L.append('\n## 4. Нет дневных свечей за период (по контрактам kept=1)\n\n| биржа | контрактов | без данных | не все 36 сут | примеры ошибок |\n|---|---|---|---|---|')
for e in EX:
    ks=[(r['ex'],r['sym']) for r in C if r['ex']==e]; nd=[k for k in ks if not d.get(k)]; part=[k for k in ks if d.get(k) and len(d[k])<36]
    ex_=collections.Counter(err.get(k,'') for k in nd).most_common(2)
    L.append(f'| {e} | {len(ks)} | {len(nd)} | {len(part)} | {ex_} |')
open(out+'/summary.md','w',encoding='utf-8').write('\n'.join(L)+'\n')
