#!/usr/bin/env python3
"""TK-380: сводка отрывов Gate vs DEX BSC. net = pnl - RT - S(coin,day); полспреда = pnl - RT - S/2. Группы: ALL и терцили монет по среднему спреду Gate-ноги (S узкий = «тяжёлые»).
Бутстрап по монето-суткам (4000). Запуск: tk380-bsc-lag-summ.py DIR OUT.json"""
import sys, csv, json, random, statistics, collections
D = sys.argv[1]
eps = list(csv.DictReader(open(f'{D}/episodes.csv'))); sp = {(r['coin'], r['kind'], int(r['day'])): r for r in csv.DictReader(open(f'{D}/spread.csv'))}
rnd = random.Random(3801)
def boot(v, B=4000):
    if len(v) < 2: return None
    ms = sorted(statistics.mean(rnd.choice(v) for _ in v) for _ in range(B)); return [round(statistics.mean(v), 1), round(ms[int(.025 * B)], 1), round(ms[int(.975 * B)], 1)]
out = {}
for kind in ('fut', 'spot'):
    cs = collections.defaultdict(list)
    for (c, k, d), r in sp.items():
        if k == kind: cs[c].append(float(r['mean_bp']))
    ms = {c: statistics.mean(v) for c, v in cs.items()}; srt = sorted(ms, key=ms.get); n = len(srt)
    ter = {'узкий (тяжёлые)': srt[:n // 3], 'средний': srt[n // 3: 2 * n // 3], 'широкий': srt[2 * n // 3:]}
    groups = {'ALL': srt, **ter}
    for X in (50, 100, 200):
        for g, cl in groups.items():
            byd = collections.defaultdict(list); RT = None
            for e in eps:
                if e['kind'] == kind and int(e['X']) == X and e['coin'] in cl and (e['coin'], kind, int(e['day'])) in sp: byd[(e['coin'], int(e['day']))].append(float(e['pnl'])); RT = float(e['RT'])
            if len(byd) < 2: continue
            gross = [statistics.mean(v) for v in byd.values()]; S = [float(sp[(k[0], kind, k[1])]['mean_bp']) for k in byd]
            out[f'{kind}|{g}|{X}'] = dict(kind=kind, group=g, X=X, coins=len({k[0] for k in byd}), units=len(byd), episodes=sum(map(len, byd.values())), RT=RT, S=round(statistics.mean(S), 1),
                                         gross=boot(gross), net=boot([a - RT - s for a, s in zip(gross, S)]), half=boot([a - RT - s / 2 for a, s in zip(gross, S)]), days_pos=sum(1 for a, s in zip(gross, S) if a - RT - s > 0))
json.dump(out, open(sys.argv[2], 'w'), ensure_ascii=False, indent=1)
for k, v in out.items(): print(k, v['coins'], v['units'], v['episodes'], 'S', v['S'], 'gross', v['gross'], 'net', v['net'], 'half', v['half'], v['days_pos'])
