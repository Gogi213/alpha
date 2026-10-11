import csv,json,re,collections,sys
F='docs/findings/'
hyp=list(csv.DictReader(open(F+'letopis-hypotheses-2026-10-11.csv',encoding='utf-8'),delimiter=';'))
rc=[json.loads(l) for l in open('docs/registry/run_cells.jsonl',encoding='utf-8')]
cells={}
for l in open('docs/registry/cells.jsonl',encoding='utf-8'):
    x=json.loads(l);cells[x['id']]=x
res=collections.defaultdict(set)
for l in open('docs/registry/results.jsonl',encoding='utf-8'):
    x=json.loads(l);res[x['cell_id']].add(x['month'])
reg=collections.defaultdict(set)
for x in rc:
    if x['hyp_id'] and x['hyp_id'].startswith('Г-') and x['cell_id'] in res: reg[x['hyp_id']].add(x['cell_id'])
p12=collections.defaultdict(list)
for fn in ('p12-cells-2026-10-06.csv','p12-cells-ext-2026-10-07.csv'):
    for r in csv.DictReader(open(F+fn,encoding='utf-8')):
        m=re.match(r'[ge](\d+)',r['family'])
        p12[('Г-%02d'%int(m.group(1)) if int(m.group(1))>=10 else 'Г-%02d'%int(m.group(1))) if m else 'ext'].append(r)
B1='ladder3x0..0.0409sw2; стоп pct2; тейк tr1x1; дедлайн 14400 с; ttl 1800; набор t-bid-btc4h-q1 (age=2700, side=bid, btc4h_max=-44.55); busy-skip off; sigma240; В-68; prob:3'
R2B={'e112','e116','e133','e65'}
rows=[]
for h in hyp:
    i=h['id'];cl=h['класс_TK024'];fam=h['семья']
    pc=p12.get(i,[]);rcells=reg.get(i,set())
    nh=len(rcells);months=sorted({m[5:] for c in rcells for m in res[c]})
    if pc:
        base='B1 П-10 §4: '+B1
        tit='; '.join(sorted({r['axes'] if 'axes' in r else r['diff_from_B1'] for r in pc}))[:300]
        ncell=len(pc)
        f=pc[0]['family']
        if pc[0]['class']=='R1': st='в Летописи' if nh else 'посчитано, не загружено'
        elif f in R2B or (f=='g95' and False): st='в счёте (R2-B, TK-115)'
        else: st='посчитано (R2 волна A), не загружено в Летопись: нет cells-файла'
        if cl!='R1' and pc[0]['class']=='R1' and not nh: st='посчитано, не загружено'
    elif nh:
        fs=sorted({(cells[c]['form'],cells[c]['set_name']) for c in rcells})
        base='пул b14 TK-040: форма×набор на клетку (упор: B1-типа, 242 кл.)'
        tit='; '.join(f'{a} | {b}' for a,b in fs)[:300]; ncell=len(fs); st='в Летописи'
    else:
        base='—';tit='—';ncell=0
        why=h['не_хватает_до_01.01-02.10'] or h['статус_пула_25.09']
        if why[:1].isdigit(): st='частично (старый движок, TK-009): Летопись '+h['месяцы_в_Летописи']+'; нет '+why
        elif why.startswith('не считается'): st='не считается — '+(h['итог_по_данным'] or h['статус_пула_25.09'])[:140]
        elif why.startswith('счёт'): st='ждёт счёта: '+why
        else: st='не считается — '+why[:140]
    rows.append(dict(id=i,название=h['формулировка'][:160],класс=cl,семья=fam,протокол=h['месяцы_по_протоколу'],базовый_стек=base,титруется=tit,клеток_в_плане=ncell,хешей_в_Летописи=nh,месяцы_в_Летописи=','.join(months),статус=st))
w=csv.DictWriter(open(F+'hyp-stack-2026-10-11.csv','w',encoding='utf-8',newline=''),fieldnames=list(rows[0]),delimiter=';');w.writeheader();w.writerows(rows)
print(collections.Counter(r['статус'][:30] for r in rows)); print(sum(r['хешей_в_Летописи'] for r in rows))
