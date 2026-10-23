#!/usr/bin/env python3
"""TK-378 v3: net = pnl - 15 бп (тейкер Gate x2) - эффективный спред Gate-ноги (по сделкам, tk378_gate_spread.py), бутстрап по монето-суткам.
Главная строка CH2 = CLAUDIA+HOOKED (чистые цены DEX); ALL4 и FONE/SI — справкой. Запуск: ...summ.py episodes.csv spread.csv OUT.json"""
import sys, csv, json, random, statistics, collections
GATE_RT = 15.0
eps = list(csv.DictReader(open(sys.argv[1])))
sp = {(r['coin'], int(r['day'])): r for r in csv.DictReader(open(sys.argv[2])) if r['mean_bp']}
rnd = random.Random(3783)
def boot(v, B=4000):
    if len(v) < 2: return None
    ms = sorted(statistics.mean(rnd.choice(v) for _ in v) for _ in range(B))
    return round(statistics.mean(v), 1), round(ms[int(.025 * B)], 1), round(ms[int(.975 * B)], 1)
GR = {'CH2': ['CLAUDIA', 'HOOKED'], 'CLAUDIA': ['CLAUDIA'], 'HOOKED': ['HOOKED'], 'FONE': ['FONE'], 'SI': ['SI'], 'ALL4': ['CLAUDIA', 'FONE', 'HOOKED', 'SI']}
out = {}
for X in (50, 100, 200):
    for g, cs in GR.items():
        byd = collections.defaultdict(list)
        for e in eps:
            if e['coin'] in cs and int(e['X']) == X and (e['coin'], int(e['day'])) in sp: byd[(e['coin'], int(e['day']))].append(float(e['pnl']))
        if not byd: continue
        gross = [statistics.mean(v) for v in byd.values()]
        s_mean = [float(sp[k]['mean_bp']) for k in byd]; s_med = [float(sp[k]['med_bp']) for k in byd]
        n_mean = [a - GATE_RT - s for a, s in zip(gross, s_mean)]; n_med = [a - GATE_RT - s for a, s in zip(gross, s_med)]
        n_half = [a - GATE_RT - s / 2 for a, s in zip(gross, s_mean)]  # оптимистично: платим полспреда за круг (вход/выход по свежей стороне)
        out[f'{g}|{X}'] = dict(group=g, X=X, episodes=sum(len(v) for v in byd.values()), units=len(byd), gross=boot(gross), spread_mean=round(statistics.mean(s_mean), 1),
                               net_spread_mean=boot(n_mean), net_spread_med=boot(n_med), net_halfspread=boot(n_half), days_pos=sum(1 for v in n_mean if v > 0))
json.dump(out, open(sys.argv[3], 'w'), ensure_ascii=False, indent=1)
for k, v in out.items(): print(k, v['episodes'], v['units'], 'gross', v['gross'], 'S', v['spread_mean'], 'net', v['net_spread_mean'], 'med', v['net_spread_med'], 'half', v['net_halfspread'], v['days_pos'])
