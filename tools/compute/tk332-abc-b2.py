#!/usr/bin/env python3
"""TK-332 П-22: B2 (потолок 3 позиции, 1 на монету), столбец D (спот S0), bootstrap по суткам, Холм по 2, причины.
Читает rounds.csv s0 тех же прогонов, что tk332-abc-summary.py; D — /data/tk0313/out."""
import glob, csv, collections, random, statistics as st, math, sys
R = '/data/tk0332-b/'
SETS = {'A': R+'outA', 'B': R+'outB4', 'C-3600': R+'outC-3600', 'C+3600': R+'outC3600',
        'C-7200': R+'outC-7200', 'C+7200': R+'outC7200', 'D': '/data/tk0313/out'}
def load(d):
    rows = []; sig = set()
    for f in glob.glob(d+'/run-*/out/*/s0/rounds.csv'):
        for r in csv.DictReader(l for l in open(f) if not l.startswith('#')):
            sig.add(r['symbol'])
            q = float(r['qty'] or 0)*float(r['entry_vwap'] or 0)
            if q <= 0: continue
            rows.append(dict(sym=r['symbol'], day=r['day_utc'], t0=int(r['t0_ns']), t1=int(r['exit_ns']),
                             usd=q, net=float(r['net_bps']), reason=r['reason'], fill=float(r['fill_frac'] or 0)))
    return rows, sig
def cap(rows, maxpos):
    """как portfolio.simulate: по t0, 1 позиция на монету, потолок maxpos (0 — нет потолка)"""
    op = []; out = []
    for r in sorted(rows, key=lambda x: x['t0']):
        op = [p for p in op if p[0] > r['t0']]
        if any(p[1] == r['sym'] for p in op): continue
        if maxpos and len(op) >= maxpos: continue
        op.append((r['t1'], r['sym'])); out.append(r)
    return out
def daily(rows):
    d = collections.defaultdict(float)
    for r in rows: d[r['day']] += r['usd']*r['net']/1e4
    return d
def sr(x):
    return st.mean(x)/st.pstdev(x)*365**.5 if len(x) > 2 and st.pstdev(x) > 0 else float('nan')
data = {k: load(v) for k, v in SETS.items()}
alld = sorted({r['day'] for k in data for r in data[k][0]})
mons = sorted({d[:7] for d in alld})
mdays = {m: [d for d in alld if d[:7] == m] for m in mons}
modes = {'raw': -1, 'free': 0, 'B2': 3}
summ = {}
print('набор  режим | Σ$ по месяцам | Σ$ | мес>0 | n сделок | монет | сутки со сделкой')
for k in SETS:
    for mn, mp in modes.items():
        t = data[k][0] if mp < 0 else cap(data[k][0], mp)
        dd = daily(t)
        ms = [sum(v for d, v in dd.items() if d[:7] == m) for m in mons]
        summ[(k, mn)] = (t, dd, ms)
        print(k, mn, ' '.join(f'{x:6.0f}' for x in ms), f'| {sum(ms):7.0f} | {sum(1 for x in ms if x > 0)}/9 | {len(t)} | {len({r["sym"] for r in t})} | {len(dd)}')
# C-среднее и сравнение с B
for mn in modes:
    cm = [sum(summ[(c, mn)][2][i] for c in ('C-3600', 'C+3600', 'C-7200', 'C+7200'))/4 for i in range(len(mons))]
    bm = summ[('B', mn)][2]
    print('B>C', mn, sum(1 for b, c in zip(bm, cm) if b > c), '/9; среднее C Σ', round(sum(cm)))
# bootstrap по суткам внутри месяца: SR месяца; агрегат = среднее месячных SR
random.seed(22); NB = 4000
def boot(dd):
    per = {m: [dd.get(d, 0.0) for d in mdays[m]] for m in mons}
    srm = {m: sr(per[m]) for m in mons}
    agg = []; low = {m: [] for m in mons}
    for _ in range(NB):
        a = []
        for m in mons:
            x = [random.choice(per[m]) for _ in per[m]]
            s = sr(x); low[m].append(s); a.append(s)
        agg.append(st.mean(a))
    q = lambda v, p: sorted(v)[int(p*len(v))]
    return srm, {m: (q(low[m], .05), q(low[m], .025)) for m in mons}, (q(agg, .05), q(agg, .025), st.mean(agg))
print('\nbootstrap (сутки, 4000), SR годовой; нижние края 5 % (90 % одност.) / 2,5 % (Холм по 2)')
for k in ('B', 'A', 'D'):
    for mn in ('free', 'B2'):
        t, dd, ms = summ[(k, mn)]
        srm, lows, agg = boot(dd)
        effn = {m: sum(1 for d in mdays[m] if d in dd) for m in mons}
        print(k, mn, 'SR мес:', ' '.join(f'{srm[m]:.1f}' for m in mons))
        print('   нижн5%:', ' '.join(f'{lows[m][0]:.1f}' for m in mons), '| агрегат (ср. SR мес) нижн5%%=%.2f нижн2,5%%=%.2f ср=%.2f' % agg)
        print('   эфф.N (сутки со сделкой):', ' '.join(str(effn[m]) for m in mons), '=', sum(effn.values()), 'из', len(alld))
# причины и издержки
print('\nпричины выхода, средний net bps:')
for k in ('A', 'B', 'D', 'C-3600'):
    rows = data[k][0]; c = collections.Counter(r['reason'] for r in rows)
    tot = len(rows)
    print(k, 'n', tot, 'net bps ср %.2f' % (sum(r['net'] for r in rows)/tot), {a: f'{b/tot:.0%}/{sum(r["net"] for r in rows if r["reason"]==a)/b:.1f}' for a, b in c.most_common(5)})
print('\nмонеты: сигнальные строки (любые), сделки, A/B/D')
for k in ('A', 'B', 'D', 'C+3600'):
    print(k, 'монет в rounds', len(data[k][1]), 'с заполнением', len({r['sym'] for r in data[k][0]}))
sa, sb, sd = data['A'][1], data['B'][1], data['D'][1]
print('B∩D', len(sb & sd), 'B∩A', len(sb & sa), 'D-B', len(sd-sb), 'A-B', len(sa-sb))
# общие монето-сутки B и D
def keyset(rows): return {(r['sym'], r['day']) for r in rows}
kb, kd, ka = keyset(data['B'][0]), keyset(data['D'][0]), keyset(data['A'][0])
print('монето-сутки со сделкой: B', len(kb), 'D', len(kd), 'A', len(ka), 'B∩D', len(kb & kd), 'B∩A', len(kb & ka))
for k in ('B', 'D', 'A'):
    com = [r for r in data[k][0] if (r['sym'], r['day']) in (kb & kd)]
    print(k, 'на общих B∩D монето-сутках: n', len(com), 'Σ$', round(sum(r['usd']*r['net']/1e4 for r in com)), 'net ср %.2f' % (sum(r['net'] for r in com)/max(1, len(com))))
