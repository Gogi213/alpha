# TK-433 v11 vs зигзаг v10 (сент.): доля общих и |Δt| старта — описательно
import csv, numpy as np, collections
v10=[r for r in csv.DictReader(open('docs/findings/pump-v10-events-2026-09.csv',encoding='utf-8')) if r['роль']=='event']
v11=list(csv.DictReader(open('docs/findings/pump-v11-events-2026-09.csv',encoding='utf-8')))
idx=collections.defaultdict(list)
for r in v11: idx[(int(r['D_мин']),r['инструмент'],r['знак'])].append((int(r['t_start_ms'])//60000,int(r['t_peak_ms'])//60000))
out=['# TK-433: v11 (окно) против зигзага v10, сентябрь, описательно\n','Событие v10 (роль event, T=5/15/60, ноги зигзага) — «общее», если у v11 той же монеты и знака есть событие, чей интервал [старт,пик] пересекает интервал ноги. Сравнение по инструменту (без рынка).\n',
'| T | D v11 | ног v10 | общих | доля | v11 событий | из них пересекают ногу v10 | доля | медиана \|Δстарт\|, мин (общие) |','|---|---|---|---|---|---|---|---|---|']
for T in ('5','15','60'):
    L=[r for r in v10 if r['T']==T]
    for D in (90,180,360,720,1440):
        com=0;dts=[]
        for r in L:
            s,p=int(r['start_min']),int(r['peak_min']); sg=r['знак'] if r['знак'] in('+','-') else ('+' if r['знак']=='1' else '-')
            best=None
            for a,b in idx.get((D,r['инструмент'],sg),[]):
                lo,hi=min(a,b),max(a,b)
                if lo<=max(s,p) and min(s,p)<=hi: 
                    d=abs(a-s); best=d if best is None else min(best,d)
            if best is not None: com+=1; dts.append(best)
        legs=collections.defaultdict(list)
        for r in L:
            sg=r['знак'] if r['знак'] in('+','-') else ('+' if r['знак']=='1' else '-')
            legs[(r['инструмент'],sg)].append((min(int(r['start_min']),int(r['peak_min'])),max(int(r['start_min']),int(r['peak_min']))))
        n11=0;c11=0
        for (d,ins,sg),v in idx.items():
            if d!=D: continue
            for a,b in v:
                n11+=1
                if any(lo<=max(a,b) and min(a,b)<=hi for lo,hi in legs.get((ins,sg),[])): c11+=1
        out.append(f"| {T} | {D} | {len(L)} | {com} | {com/max(len(L),1):.2f} | {n11} | {c11} | {c11/max(n11,1):.2f} | {np.median(dts) if dts else float('nan'):.0f} |")
open('docs/findings/pump-v11-vs-zigzag-2026-10-28.md','w',encoding='utf-8',newline='\n').write('\n'.join(out)+'\n')
