# контракты kept=1 -> contracts.csv (asset,class,ex,sym,in_set,downloaded,gb). in_set = как в delta.py (статус ok/mult_diff/no_price, тип A/B)
import json,csv,sys
d='../p40-universe-2026-11-09/'
D={k:set(v) for k,v in json.load(open(d+'downloaded_syms.json')).items()}
K=json.load(open(d+'recon_kept.json'))
sz={(r['ex'],r['sym']):float(r['mb_per_day']) for r in csv.DictReader(open(d+'probe_all_pairs_mult.csv',encoding='utf-8'))}
w=csv.writer(open('contracts.csv','w',newline='',encoding='utf-8'));w.writerow(['asset','class','ex','sym','in_set','downloaded','gb'])
for a,c,ex,s,st,mn,me,t in K:
    ins=int(st in('ok','mult_diff','no_price') and t in('A','B'))
    w.writerow([a,c,ex,s,ins,int(s in D.get(ex,())),round(sz[(ex,s)]*36/1000,3)])
