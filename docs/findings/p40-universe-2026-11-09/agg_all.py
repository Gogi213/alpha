# Сводка пробы probe_all.jsonl: фильтр по датам (есть стакан хотя бы на одной из 6 дат), актив на ≥2 биржах, размер по выборке (2 часа 12:00 ×24 ×36 сут).
# Запуск: python3 -I agg_all.py probe_all.jsonl > probe_all_summary.md ; пары -> probe_all_pairs.csv
import json,sys,collections,csv
R=[json.loads(l) for l in open(sys.argv[1],encoding='utf-8')]
EXS=['binance_futures','okx_futures','bitget_futures','aster_futures','hyperliquid_futures','bybit_futures','gate_futures']
for r in R:
    v=list(r['avail'].values()); r['n_av']=sum(v); r['ok']=r['n_av']>0
    s=[x for x in r['sz'].values() if x]
    r['mb_day']=(sum(s)/len(s)/1e6*(24 if r['ex'] not in('bybit_futures',) else 1)) if s else 0.0
by=collections.defaultdict(dict)
for r in R:
    if r['ok']: by[r['asset']][r['ex']]=r
keep={a for a,d in by.items() if len(d)>=2}
cls={r['asset']:r['class'] for r in R}
tab=collections.defaultdict(lambda:[0,0.0]); tot0=collections.Counter(); 
for r in R: tot0[(r['ex'],r['class'])]+=1
for a in keep:
    for ex,r in by[a].items(): t=tab[(ex,cls[a])]; t[0]+=1; t[1]+=r['mb_day']*36/1000
print(f'Пар актив×биржа в пробе: {len(R)}; со стаканом хотя бы на одной из 6 дат: {sum(r["ok"] for r in R)}; активов до фильтра {len({r["asset"] for r in R})}, после (≥2 бирж со стаканом в периоде): {len(keep)}')
print('\n| биржа | класс | контрактов в списке | со стаканом в периоде и актив на ≥2 | ГБ на 36 сут (по выборке) |\n|---|---|---|---|---|')
for ex in EXS:
    for c in sorted({k[1] for k in tot0}):
        if tot0[(ex,c)]: n,g=tab[(ex,c)]; print(f'| {ex} | {c} | {tot0[(ex,c)]} | {n} | {g:.0f} |')
print('\nИтого ГБ (36 сут):',round(sum(v[1] for v in tab.values())),'; не-крипта:',round(sum(v[1] for k,v in tab.items() if k[1]!='крипта')))
print('Активов по классам после фильтра:',dict(collections.Counter(cls[a] for a in keep)))
w=csv.writer(open('probe_all_pairs.csv','w',newline='',encoding='utf-8')); w.writerow(['asset','class','ex','sym','dates_av_of_6','mb_per_day','kept'])
for r in R: w.writerow([r['asset'],r['class'],r['ex'],r['sym'],r['n_av'],round(r['mb_day'],2),int(r['ok'] and r['asset'] in keep)])
