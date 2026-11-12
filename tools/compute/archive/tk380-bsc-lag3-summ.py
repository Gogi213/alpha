#!/usr/bin/env python3
"""TK-380 v3: сводка фьюч-отрывов Gate vs DEX по BBO стакана TK-379. Тейкер: net = pnl_x - RT (спред уже в pnl_x: вход по аску/биду, выход по противоположной стороне).
Мейкер-вход: pnl_m (только исполненные: сделка СКВОЗЬ цену лимита до закрытия эпизода) - RT_мейкер(=RT-7,5 как допущение, показать и без); доля исполнений.
Группы: ALL и терцили по медиане BBO-спреда (узкий = «тяжёлые»). Бутстрап по монето-суткам (4000). Запуск: tk380-bsc-lag3-summ.py DIR OUT.json"""
import sys, csv, json, random, statistics, collections
D = sys.argv[1]
eps = [e for e in csv.DictReader(open(f'{D}/episodes.csv')) if e['kind'] == 'fut' and e.get('pnl_x', '') != '']
sm = {s['coin']: s for s in json.load(open(f'{D}/summ.json'))['summ'] if s['kind'] == 'fut' and s.get('bbo_spread_med_bp') is not None}
rnd = random.Random(3802)
def boot(v, B=4000):
    if len(v) < 2: return None
    ms = sorted(statistics.mean(rnd.choice(v) for _ in v) for _ in range(B)); return [round(statistics.mean(v), 1), round(ms[int(.025 * B)], 1), round(ms[int(.975 * B)], 1)]
srt = sorted(sm, key=lambda c: sm[c]['bbo_spread_med_bp']); n = len(srt)
groups = {'ALL': srt, 'узкий (тяжёлые)': srt[:n // 3], 'средний': srt[n // 3: 2 * n // 3], 'широкий': srt[2 * n // 3:]}
out = {}
for X in (50, 100, 200):
    for g, cl in groups.items():
        cs = set(cl); byd = collections.defaultdict(list)
        for e in eps:
            if int(e['X']) == X and e['coin'] in cs: byd[(e['coin'], int(e['day']))].append(e)
        if len(byd) < 2: continue
        RT = float(next(iter(byd.values()))[0]['RT'])
        tk = [statistics.mean(float(e['pnl_x']) for e in v) for v in byd.values()]
        mk = [statistics.mean(float(e['pnl_m']) for e in v if e['filled'] == '1') for v in byd.values() if any(e['filled'] == '1' for e in v)]
        nf = sum(1 for v in byd.values() for e in v if e['filled'] == '1'); ne = sum(len(v) for v in byd.values())
        out[f'{g}|{X}'] = dict(group=g, X=X, coins=len({k[0] for k in byd}), units=len(byd), episodes=ne, RT=RT,
                               spread_med=round(statistics.mean(sm[c]['bbo_spread_med_bp'] for c in cl), 2),
                               taker_gross=boot(tk), taker_net=boot([a - RT for a in tk]), days_pos=sum(1 for a in tk if a - RT > 0),
                               fill_rate=round(nf / ne, 3), maker_gross=boot(mk), maker_net_RT=boot([a - RT for a in mk]), maker_net_RTm75=boot([a - (RT - 7.5) for a in mk]))
json.dump(out, open(sys.argv[2], 'w'), ensure_ascii=False, indent=1)
for k, v in out.items(): print(k, v['coins'], v['units'], v['episodes'], 'S', v['spread_med'], 'taker', v['taker_gross'], v['taker_net'], 'fill', v['fill_rate'], 'maker', v['maker_gross'], v['maker_net_RT'], v['maker_net_RTm75'])
