import csv,json,re,collections
F='docs/findings/'
old=list(csv.DictReader(open(F+'hyp-stack-2026-10-11.csv',encoding='utf-8'),delimiter=';'))
cells=[json.loads(l) for l in open('docs/registry/cells.jsonl',encoding='utf-8')]
cid={c['id']:c for c in cells}
byname={c['set_name'].split('/',1)[-1]:c for c in cells}
rc=[json.loads(l) for l in open('docs/registry/run_cells.jsonl',encoding='utf-8')]
hc=collections.defaultdict(set)
for x in rc: hc[x['hyp_id']].add(x['cell_id'])
p12=collections.defaultdict(list)
for fn in('p12-cells-2026-10-06.csv','p12-cells-ext-2026-10-07.csv'):
    for r in csv.DictReader(open(F+fn,encoding='utf-8')):
        m=re.match(r'[ge](\d+)',r['family'])
        p12['Г-%02d'%int(m.group(1)) if m else 'ext'].append(r)
COLS=['вход','стоп','тейк','дедлайн','ttl','набор','возраст','btc','σ','задержка','очередь','busy','свой']
B1=dict(вход='ladder3x0..0.0409sw2',стоп='pct2',тейк='tr1x1',дедлайн='14400',ttl='1800',набор='t-bid-btc4h-q1',возраст='2700',btc='btc4h≤−44,55',σ='sigma240',задержка='В-68',очередь='prob:3',busy='off',свой='')
def pool(c):
    d=dict(B1);p=c.get('params',{})
    t=c['form'].split('-');i=next((k for k,s in enumerate(t) if re.fullmatch(r'\d{4,6}',s)),None)
    if i is None: return d
    pre=t[:i-1];d['тейк']=t[i-1];d['дедлайн']=t[i];d['ttl']=t[i+1].replace('ttl','') if i+1<len(t) else '1800'
    suf='-'.join(t[i+2:])
    st=[s for s in pre if re.fullmatch(r'pct[\d.]+',s)]
    d['стоп']=st[0] if st else (c.get('wall_exit') or pre[-1] if pre else '—')
    ent=[s for s in pre if s not in st];d['вход']='-'.join(ent) or 'по умолчанию'
    g,_,s=c['set_name'].partition('/');d['набор']=g
    a=p.get('hdr_min_age_secs');d['возраст']=re.sub(r'\D','',a) if a else d['возраст']
    if p.get('hdr_ctx'): d['btc']=p['hdr_ctx']
    if c.get('latency_ms'): d['задержка']='В-68 %s мс'%c['latency_ms']
    own=[('набор-конф',s)]
    if suf: own.append(('форма+',suf))
    if p.get('hdr_usd_min','None')!='None': own.append(('usd_min',re.sub(r'\D*([\d.]+)\D*',r'\1',p['hdr_usd_min'])))
    if p.get('hdr_min_flow_pct','None')!='None': own.append(('flow%',re.sub(r'[^\d.]','',p['hdr_min_flow_pct'])))
    if p.get('hdr_signal')=='touch': own.append(('сигнал','touch'))
    d['_own']=own
    return d
def p12cell(r):
    d=dict(B1)
    if 'axes' in r: d['_own']=[tuple(kv.split('=',1)) for kv in r['axes'].split(';') if '=' in kv]
    else:
        m=re.match(r'(\w+) (.+?)→(.+)',r['diff_from_B1'])
        if m:
            k={'stop':'стоп','take':'тейк','dl':'дедлайн','deadline':'дедлайн'}.get(m.group(1))
            if k: d[k]=m.group(3)
            else: d['_own']=[(m.group(1),m.group(3))]
        else: d['_own']=[('diff',r['diff_from_B1'])]
    return d
def agg(recs):
    out={};T={}
    for c in COLS[:-1]:
        v=sorted({r[c] for r in recs},key=lambda x:(len(x),x))
        out[c]=v
    own=collections.defaultdict(set)
    for r in recs:
        for k,v in r.get('_own',[]): own[k].add(v)
    return out,own
def fmt(vs,md):
    if len(vs)==1: return vs[0] if vs[0] else '—'
    s='|'.join(vs[:12])+('|…+%d'%(len(vs)-12) if len(vs)>12 else '')
    return '**T:'+s+'**' if md else 'T:'+s
def fown(own,md):
    if not own: return '—'
    parts=[]
    for k,v in own.items():
        v=sorted(v,key=lambda x:(len(x),x));
        parts.append(k+'='+(fmt(v,md) if len(v)>1 else v[0]))
    return '; '.join(parts)

PY='посчитано без хешей (П-10 §6.2, Python на закрытиях/сигналах B1)'
PD='план П-10 §6.1 (код есть); клеток в реестре под этой Г нет'
OV={
'Г-11':({'признак':['|ход монеты−BTC| 1ч'],'терцили':['q33','q67']},PY),'Г-62':({'признак':['ER 60м'],'терцили':['q33','q67']},PY),
'Г-74':({'признак':['разгон: max high 5 баров'],'терцили':['q33','q67']},PY),'Г-76':({'признак':['удаление от min60/σ240'],'терцили':['q33','q67']},PY),
'Г-22':({'признак':['оборот 24ч'],'терцили':['thin33','thin67']},PY),'Г-54':({'признак':['оборот 24ч'],'терцили':['thin33','thin67']},PY),
'Г-123':({'оборот24ч_доля':['1%']},PY),'Г-48':({'признак':['доля стопов монеты 14 сут'],'терцили':['q33','q67']},PY),
'Г-49':({'признак':['доля стопов монеты 14 сут'],'терцили':['q33','q67']},PY),'Г-31':({'признак':['положение в диапазоне 60м'],'терцили':['q33','q67']},PY),
'Г-47':({'окно_истории':['24ч']},PY),'Г-14':({'пропуск_часов_UTC':['16–17','00','02–06+21–23','сб–вс']},PY),
'Г-45':({'до_фандинга_мин':['≤60']},PY),'Г-120':({'убытков_подряд':['5'],'блок':['24ч']},PY),
'Г-24':({'RV5м/RV4ч':['2,5','4']},PY),'Г-25':({'люстра':['2,5σ4ч'],'RV_суток_bps':['≤262']},PY),'Г-109':({'доля_на_1:1':['0,5']},PY),
'Г-89':({'usd_min':['верхняя треть']},PD),'Г-147':({'signal':['touch']},PD),'Г-129':({'entry_ttl':['300','60']},PD),
'Г-110':({'early_exit_s':['1','2','3']},PD),'Г-104':({'exit-form':['gone50','gone90'],'be':['да']},PD),'Г-107':({'usd_min':['нижняя треть']},PD),
'Г-10':({'btc_окно_ч':['1','2','3'],'btc_порог':['−21,17','−30,56','−38,75']},'посчитано (пул b14, p07a-h5; в реестре под Г-85а — привязка по имени набора); в плане и П-10 §6.1'),
'Г-85':({'вход':['single@fr+1','+2','+3']},'посчитано (пул b14, p07a-h2; в реестре под Г-85а)'),
}
rows=[];extra=collections.Counter()
for o in old:
    h=o['id'];recs=[]
    cs=[cid[x] for x in hc.get(h,()) if x in cid]
    pr=p12.get(h,[])
    names={r['cell'] for r in pr}
    for c in cs:
        nm=c['set_name'].split('/',1)[-1]
        if nm in names or c['set_name'].startswith(('p12-','r2a')): continue
        recs.append(pool(c))
    for r in pr: recs.append(p12cell(r))
    o=dict(o)
    if not recs:
        for c in COLS: o['m_'+c]=''
        o['пояснение']=''
    else:
        out,own=agg(recs)
        for c in COLS[:-1]: o['m_'+c]=out[c]
        o['m_свой']=own;o['клеток_в_плане']=len(recs)
    if h in OV and not recs:
        own,st=OV[h];o['m_свой']={k:set(v) for k,v in own.items()}
        for c in COLS[:-1]: o['m_'+c]=[B1[c]]
        o['клеток_в_плане']=max(len(v) for v in own.values());o['статус']=st
    if o['статус'].startswith('не считается — класс П'): o['статус']='вне пакета П-10: источник не задаёт операционализацию/параметр (П-10 §6.3 — список причин)'
    rows.append(o)
def cellstr(o,c,md):
    if 'm_'+c not in o: return ''
    v=o['m_'+c]
    if c=='свой': return fown(v,md) if v or isinstance(v,dict) else ''
    return fmt(v,md) if v else '—'
cl=lambda s:s.replace('|','/')
with open(F+'hyp-stack-2026-10-11.csv','w',encoding='utf-8',newline='') as f:
    w=csv.writer(f,delimiter=';');w.writerow(['id','название','класс','семья','протокол']+COLS+['клеток_в_плане','хешей_в_Летописи','статус'])
    for o in rows:
        w.writerow([o['id'],o['название'],o['класс'],o['семья'],o['протокол']]+[cellstr(o,c,False) if 'm_'+c in o and o['m_'+c] not in('',[]) else '' for c in COLS]+[o['клеток_в_плане'],o['хешей_в_Летописи'],o['статус']])
with open(F+'hyp-stack-2026-10-11.md','w',encoding='utf-8') as f:
    f.write('# Матрица гипотеза × гиперпараметр (148 гипотез Летописи), 11.10\n\nБаза B1 (П-10 §4): '+'; '.join(f'{k}={v}' for k,v in B1.items() if v)+'. Ячейка: обычный текст — заморожено; **T:a|b** — титруется; «—» не применимо; пусто — клеток нет. Столбец «свой» — собственные параметры механизма (N, u, K, W, q…). CSV — та же матрица (`T:` вместо жирного).\n\n')
    for k in ['R1','R2','Д','П','Е','Н','О','М']:
        rs=[o for o in rows if o['класс']==k]
        if not rs: continue
        f.write(f'## Класс {k} ({len(rs)})\n\n| Г | название | '+' | '.join(COLS)+' | кл. | хешей | статус |\n|'+'---|'*(len(COLS)+5)+'\n')
        for o in rs:
            f.write(f"| {o['id']} | {cl(o['название'][:70])} | "+' | '.join(cl(cellstr(o,c,True)) for c in COLS)+f" | {o['клеток_в_плане']} | {o['хешей_в_Летописи']} | {cl(o['статус'][:110])} |\n")
        f.write('\n')
print(collections.Counter(o['класс'] for o in rows),sum(1 for o in rows if 'm_вход' in o and o['m_вход']))
