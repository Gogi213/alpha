#!/usr/bin/env python3
"""Признаки на входе и судьба стены против результата сделки — по месяцам (владелец 25.09: разбор убытка и
микроструктура). Сделки — из `loss-days.py --csv` (как в счёте дашборда), признаки — атомы `loss-atoms.py` той же
формы (рынок, стена, касания, судьба стены); сопоставление по монете и минуте входа. Пороги — терцили по обоим
месяцам вместе (одни для обоих); «выход при снятии» — цена рынка в минуту снятия (`mid_at_fate_bps`).
Чтение на тех же данных, не вердикт.

    cd <каталог с loss-days.csv и atoms-{aug,hist,rec}.csv> && python3 loss-features.py
"""
import csv, datetime as dt
from collections import defaultdict
LOCAL=dt.timezone(dt.timedelta(hours=4))
f=lambda x: float(x) if x not in ('',None,'None') else None
acc={}
for r in csv.DictReader(open('loss-days.csv',encoding='utf-8')):
    acc[(r['month'],r['symbol'],r['t0_local'])]=r
def load(n,m):
    out=[]
    for r in csv.DictReader(open(f'atoms-{n}.csv',encoding='utf-8')):
        k=(m,r['symbol'],dt.datetime.fromtimestamp(int(r['t0_ns'])/1e9,LOCAL).strftime('%d.%m %H:%M'))
        if k in acc: r['pnl']=f(acc[k]['pnl_usd']); r['btc_low']=f(acc[k]['btc_low_bps']); out.append(r)
    return out
M={'август':load('aug','август'),'сентябрь':load('hist','сентябрь')+load('rec','сентябрь')}
for m,rows in M.items(): print(m,'сопоставлено',len(rows),'сд',f"{sum(r['pnl'] for r in rows):+.0f}$")
def buckets(title, fn, order):
    print(f"\n== {title}")
    for m,rows in M.items():
        g=defaultdict(list)
        for r in rows:
            k=fn(r)
            if k is not None: g[k].append(r)
        print(f"  {m}: "+' | '.join(f"{k}: {len(g[k])} сд {sum(r['pnl'] for r in g[k]):+.0f}$ стоп {sum(r['reason']=='stop' for r in g[k])}" for k in order if g[k]))
def fm(r):
    if r['wall_fate']!='сняли': return None
    v=f(r['wall_fate_min']); return None if v is None else ('≤15 мин' if v<=15 else ('15–60' if v<=60 else '>60'))
buckets('Стену сняли — через сколько минут после входа', fm, ['≤15 мин','15–60','>60'])
def terc(title, key, rev=False):
    # границы — терцили по обоим месяцам вместе (одни пороги для обоих)
    vals=sorted(f(r[key]) for rows in M.values() for r in rows if f(r[key]) is not None)
    if len(vals)<30: return
    a,b=vals[len(vals)//3], vals[2*len(vals)//3]
    fn=lambda r: None if f(r[key]) is None else ('низ' if f(r[key])<=a else ('середина' if f(r[key])<=b else 'верх'))
    buckets(f"{title} (терцили: ≤{a:.0f} / ≤{b:.0f} / выше)", fn, ['низ','середина','верх'])
for k,t in [('btc_1h','BTC за 1 ч до входа, bps'),('btc_4h','BTC за 4 ч до входа, bps'),('pool_1h','Пул за 1 ч, bps'),('eth_4h','ETH за 4 ч, bps'),
            ('wall_age_min','Возраст стены, мин'),('wall_size_usd','Размер стены, $'),('strength_w20','Сила стены ×соседи, %'),('flow_1h_lots','Поток за 1 ч, лоты'),
            ('arm_dist_bps','Расстояние до стены при взводе, bps'),('repeat_before','Сколько раз цену уже подводило к стене'),('flow_proxy_traded_during','Проторговано в стену на прошлом касании, лоты'),
            ('entry_vs_wall_bps','Вход над стеной, bps'),('hour_utc','Час UTC')]:
    terc(t,k)

print("\n== Если бы выходили в момент снятия стены (цена рынка в ту минуту), по окнам снятия")
for m,rows in M.items():
    out=[]
    for lo,hi,lab in ((0,15,'≤15 мин'),(15,60,'15–60'),(60,1e9,'>60')):
        g=[r for r in rows if r['wall_fate']=='сняли' and f(r['wall_fate_min']) is not None and lo<f(r['wall_fate_min'])<=hi and f(r['mid_at_fate_bps']) is not None]
        act=sum(r['pnl'] for r in g)
        alt=0.0
        for r in g:
            e,x=f(r['entry_px']),f(r['exit_px']); gross=(x/e-1)*1e4
            usd=r['pnl']/(f(r['net_bps'])/1e4) if f(r['net_bps']) else 0
            alt+= (f(r['net_bps'])+f(r['mid_at_fate_bps'])-gross)/1e4*usd
        out.append(f"{lab}: {len(g)} сд, как было {act:+.0f}$ → выход при снятии {alt:+.0f}$")
    print(f"  {m}: "+' | '.join(out))
