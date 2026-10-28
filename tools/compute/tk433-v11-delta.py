# TK-433 v11 дельта (возврат Судьи 07:01): X_D, воронка, распределение длительностей из уже посчитанного
import json, csv, statistics as st, sys
import numpy as np
out=[]
xq=json.load(open('data/tk433v11/s30/xq.json',encoding='utf-8'))
fn=json.load(open('data/tk433v11/s30/funnel-2026-09.json',encoding='utf-8'))
out.append('## X_D (сентябрь, % хода; q99 — главный, q98/q99,5 — описательно)\n\n| D, мин | знак | окон | q98 | q99 | q99,5 |\n|---|---|---|---|---|---|')
for k,v in xq.items():
    d,s=k.split('|'); out.append(f"| {d} | {s} | {v['n_окон']:,} | {v['q98']} | {v['q99']} | {v['q99.5']} |".replace(',',' '))
out.append('\n## Воронка сентября по D (знаки вместе; ключ «X» = q98 и q99)\n\n| D\X | окон-событий | после E1 (фитили) | E1,E3 | E1,E3,E4 | представителей | в парах памп→дамп |\n|---|---|---|---|---|---|---|')
for k,v in fn.items():
    out.append(f"| {k} | "+" | ".join(str(v[c]) for c in ['окон-событий','после E1','после E1,E3','после E1,E3,E4','представителей','в парах памп→дамп'])+" |")
out.append('\nПримечание: ключи funnel — X из сетки q98/q99 по обоим знакам (D90: X4.4=q99 дамп, X5.1=q99 памп; …). Август: файла воронки нет (считался без неё) — см. «Не сделано».')
for m in ('09','08'):
    rows=list(csv.DictReader(open(f'docs/findings/pump-v11-events-2026-{m}.csv',encoding='utf-8')))
    out.append(f'\n## Длительность событий, мин, месяц {m} (P10/P50/P90, N=строк события; склейка в файле уже применена)\n\n| D | знак | N | P10 | P50 | P90 | макс |\n|---|---|---|---|---|---|---|')
    keys=sorted({(int(r['D_мин']),r['знак']) for r in rows})
    for D,s in keys:
        a=np.array([float(r['длит_мин']) for r in rows if int(r['D_мин'])==D and r['знак']==s])
        p=np.percentile(a,[10,50,90]); out.append(f"| {D} | {s} | {len(a)} | {p[0]:.0f} | {p[1]:.0f} | {p[2]:.0f} | {a.max():.0f} |")
    # согласованность N: всего / с контролями
    r=list(csv.DictReader(open(f'docs/findings/pump-v11-{"sept" if m=="09" else "aug"}-r-2026-10-28.csv',encoding='utf-8')))
    out.append(f'\n### N события, месяц {m}: всего (events.csv) / с мерой NATR1_14 pre (есть норма и контроли)\n\n| D | знак | всего | с мерой |\n|---|---|---|---|')
    for x in r:
        if x['мера']=='NATR1_14' and x['положение']=='pre':
            out.append(f"| {x['D']} | {x['знак']} | {x['событий_всего']} | {x['N']} |")
open('docs/findings/pump-v11-delta-2026-10-28.md','w',encoding='utf-8',newline='\n').write('# TK-433 v11: X_D, воронка, длительности, N (дельта по возврату Судьи 07:01)\n\nИсточник: data/tk433v11/s30/{xq,funnel-2026-09}.json, pump-v11-events-*.csv, pump-v11-{sept,aug}-r-*.csv. Код: tools/compute/tk433-v11-delta.py.\n\n'+'\n'.join(out)+'\n')
