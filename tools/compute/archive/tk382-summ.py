#!/usr/bin/env python3
"""TK-382: сводка отрывов >= T % по сетям. Бутстрап по монето-суткам (4000). Группы: ALL / без «тяжёлых» (терциль с узким медианным спредом BBO по всем монетам-сетям) / знак.
Запуск: tk382-summ.py INDIR OUT_PREFIX   (INDIR/<chain>/episodes.csv, coins.csv) -> OUT_PREFIX.txt, OUT_PREFIX.csv, OUT_PREFIX-coins.csv"""
import sys, csv, glob, os, random, statistics, collections
IN, OUT = sys.argv[1], sys.argv[2]
eps = []; sp = {}
for d in sorted(glob.glob(f'{IN}/*/')):
    ch = os.path.basename(d.rstrip('/\\'))
    if os.path.exists(d + 'episodes.csv'): eps += list(csv.DictReader(open(d + 'episodes.csv')))
    if os.path.exists(d + 'coins.csv'):
        for r in csv.DictReader(open(d + 'coins.csv')):
            if r.get('spread_med_bp'): sp[(ch, r['coin'])] = float(r['spread_med_bp'])
srt = sorted(sp.values()); t1 = srt[len(srt) // 3] if srt else 0
heavy = {k for k, v in sp.items() if v <= t1}
def f(x): return None if x in ('', None) else float(x)
for e in eps:
    e['heavy'] = (e['chain'], e['coin']) in heavy
    for k in ('net', 'pnl_x', 'mae', 'ctrl', 'fund', 'dev0', 'dur_bins'): e[k] = f(e[k])
    e['key'] = (e['chain'], e['coin'], e['day'])
rng = random.Random(1)
def boot(rows, key, B=4000):
    g = collections.defaultdict(list)
    for e in rows:
        if e['net'] is not None: g[e['key']].append(e['net'])
    ks = list(g)
    if len(ks) < 2: return None
    s = {k: (sum(v), len(v)) for k, v in g.items()}
    ms = []
    for _ in range(B):
        a = b = 0
        for _ in ks:
            x = s[rng.choice(ks)]; a += x[0]; b += x[1]
        ms.append(a / b)
    ms.sort(); return ms[int(B * .025)], ms[int(B * .975)]
def mean(v): v = [x for x in v if x is not None]; return statistics.mean(v) if v else float('nan')
def row(name, rows):
    n = len(rows)
    if n == 0: return None
    ci = boot(rows, 'net'); ci = ci or (float('nan'), float('nan'))
    cd = len({e['key'] for e in rows}); top = collections.Counter(e['coin'] for e in rows).most_common(1)[0]
    maes = sorted(e['mae'] for e in rows if e['mae'] is not None)
    return dict(grp=name, n=n, coin_days=cd, coins=len({(e['chain'], e['coin']) for e in rows}), net=round(mean([e['net'] for e in rows]), 1), lo=round(ci[0], 1), hi=round(ci[1], 1),
                med_net=round(statistics.median([e['net'] for e in rows]), 1), gross=round(mean([e['pnl_x'] for e in rows]), 1), fund=round(mean([e['fund'] for e in rows]), 2), ctrl=round(mean([e['ctrl'] for e in rows]), 1),
                conv=round(sum(int(e['conv']) for e in rows) / n, 2), doubled=round(sum(int(e['doubled']) for e in rows) / n, 2), stale=round(sum(int(e['stale']) for e in rows) / n, 2),
                dur_min=round(mean([e['dur_bins'] for e in rows]) * 10 / 60, 1), mae_mean=round(mean(maes), 0), mae_p90=round(maes[int(len(maes) * .9)], 0) if maes else None, top_coin=f'{top[0]} {top[1] / n:.0%}')
out = []
for T in (2.0, 3.0, 5.0):
    for H in ('30м', '2ч', '8ч'):
        base = [e for e in eps if float(e['T']) == T and e['H'] == H]
        for nm, flt in (('все', lambda e: True), ('без тяжёлых', lambda e: not e['heavy']), ('все, не stale', lambda e: not int(e['stale'])), ('шорт фьюча', lambda e: int(e['sg']) < 0), ('лонг фьюча', lambda e: int(e['sg']) > 0), ('без тяжёлых, шорт', lambda e: not e['heavy'] and int(e['sg']) < 0)):
            r = row(f'T{T:g}% H{H} {nm}', [e for e in base if flt(e)])
            if r: out.append(r)
if out:
    w = csv.DictWriter(open(OUT + '.csv', 'w', newline=''), list(out[0])); w.writeheader(); w.writerows(out)
with open(OUT + '.txt', 'w') as t:
    t.write(f'эпизодов всего {len(eps)}; тяжёлые = спред <= {t1:.2f} бп (терциль), монет-сетей {len(sp)}, тяжёлых {len(heavy)}\n')
    for r in out: t.write(' | '.join(f'{k}={v}' for k, v in r.items()) + '\n')
    # по суткам и по сетям для T2 H2ч все
    for ch in sorted({e['chain'] for e in eps}):
        r = row(f'T2% H2ч сеть {ch}', [e for e in eps if e['chain'] == ch and float(e['T']) == 2.0 and e['H'] == '2ч'])
        if r: t.write(' | '.join(f'{k}={v}' for k, v in r.items()) + '\n')
    for d in range(15, 23):
        r = row(f'T2% H2ч сутки {d}.10', [e for e in eps if int(e['day']) == d and float(e['T']) == 2.0 and e['H'] == '2ч'])
        if r: t.write(' | '.join(f'{k}={v}' for k, v in r.items()) + '\n')
    # монеты: топ по числу эпизодов и по суммарному net (T2 H2ч)
    cc = collections.defaultdict(list)
    for e in eps:
        if float(e['T']) == 2.0 and e['H'] == '2ч': cc[(e['chain'], e['coin'])].append(e['net'])
    t.write('топ монет T2% H2ч по числу эпизодов (сеть, монета, n, mean net, спред):\n')
    for k, v in sorted(cc.items(), key=lambda x: -len(x[1]))[:15]: t.write(f'  {k} n={len(v)} net={mean(v):.1f} spread={sp.get(k)}\n')
with open(OUT + '-coins.csv', 'w', newline='') as t:
    w = csv.writer(t); w.writerow(['chain', 'coin', 'spread_med_bp', 'heavy']); [w.writerow([k[0], k[1], v, int(k in heavy)]) for k, v in sorted(sp.items())]
