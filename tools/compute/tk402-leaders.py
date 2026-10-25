#!/usr/bin/env python3
"""TK-402 доработка по возврату Судьи: (3) net по суткам 15–22.10 для топ-клеток max-T (t>=3,5, >=5/7), (4) лента лидеров: расхождение d, возраст котировок, спреды.
Запуск (calc): /data/tk0380/npvenv/bin/python tk402-leaders.py days OUT.csv [maxt.csv s2dir; для A=1 с: maxtA1000.csv /data/tk0402/s2a1000] | tape COIN DAY"""
import sys, csv, os, importlib.util, numpy as np
D0 = os.path.dirname(os.path.abspath(__file__))
sp = importlib.util.spec_from_file_location('s2', D0 + '/tk402-step2.py'); S2 = importlib.util.module_from_spec(sp); sp.loader.exec_module(S2)
CIRCLE = {'full': 2 * (5.0 + 5.5), 'disc35': 2 * (5.0 * 0.65 + 5.5)}; H0 = 1792022400000 // 3600000
def days(out, src='/data/tk0402/maxt.csv', npz='/data/tk0402/s2'):
    rows = [r for r in csv.DictReader(open(src)) if int(r['days_pos_of7']) >= 5 and float(r['t_hourly173']) >= 3.5]
    cache = {}; res = []
    for r in rows:
        z = cache.get(r['coin']) or cache.setdefault(r['coin'], np.load(f"{npz}/{r['coin']}.npz"))
        key = f"{r['fee']},{r['k']},{r['D']},{r['T']}"; g = [x for x in z['grid']]; i = list(g).index(key)
        circ = CIRCLE[r['fee']] * float(r['k']) / float(r['k'])  # круг без k: порог = k*круг, комиссия платится на круг
        net = np.zeros(192); cnt = np.zeros(192)
        for s in range(len(z['segs'])):
            a = r['anchor']
            if f's{s}_{a}_sg' not in z.files: continue
            o = int(z[f's{s}_t0'][0] // 3600000) - H0; sg = z[f's{s}_{a}_sg'][i]; n = len(sg)
            net[o:o + n] += sg - z[f's{s}_{a}_cnt'][i] * circ + z[f's{s}_{a}_fg'][i] * 1e-4  # fg в npz в 1e4 раз велик (см. FGFIX в tk402-maxt.py); cnt[o:o + n] += z[f's{s}_{a}_cnt'][i]
        dn = [net[24 * k:24 * k + 24].sum() for k in range(8)]; dc = [int(cnt[24 * k:24 * k + 24].sum()) for k in range(8)]
        res.append([r['coin'], r['anchor'], key.replace(',', ';'), r['t_hourly173'], r['p_stepdown']] + ['%.0f' % x for x in dn] + dc)
    with open(out, 'w') as f:
        f.write('coin,anchor,cell,t,p,' + ','.join(f'net_{d}' for d in range(15, 23)) + ',' + ','.join(f'n_{d}' for d in range(15, 23)) + '\n')
        for x in res: f.write(','.join(map(str, x)) + '\n')
    print(len(res), 'клеток ->', out)
def tape(coin, day):
    sym = coin + 'USDT'; g = S2.gate_day(coin, day); b = S2.bybit_day(sym, day)
    ts, gb, ga, bb, ba, ok, d, *_ = S2.tape(g, b)
    ig = np.searchsorted(g[0], ts, side='right') - 1; ib = np.searchsorted(b[0], ts, side='right') - 1
    ageg = np.where(ig >= 0, ts - g[0][np.clip(ig, 0, None)], -1); ageb = np.where(ib >= 0, ts - b[0][np.clip(ib, 0, None)], -1)
    sg = (ga - gb) / ((ga + gb) / 2) * 1e4; sb = (ba - bb) / ((ba + bb) / 2) * 1e4
    m = ok > 0; print(coin, day, 'лента', len(ts), 'ok %.3f' % m.mean(), 'событий Gate', len(g[0]), 'Bybit', len(b[0]))
    q = lambda x: np.nanpercentile(x[m], [5, 50, 95, 99]).round(1).tolist()
    print(' d бп 5/50/95/99:', q(d), ' |d|>21:', int((np.abs(d[m]) > 21).sum()), ' |d|>100:', int((np.abs(d[m]) > 100).sum()))
    print(' спред Gate бп:', q(sg), ' спред Bybit бп:', q(sb))
    print(' возраст Gate мс:', q(ageg.astype(float)), ' возраст Bybit мс:', q(ageb.astype(float)))
    fr = m & (ageg <= 1000) & (ageb <= 1000); print(' свежие (оба ≤1 с): доля времени %.3f, |d|>21: %d, |d|>100: %d, d 5/50/95 бп: %s' % (fr.mean(), int((np.abs(d[fr]) > 21).sum()), int((np.abs(d[fr]) > 100).sum()), np.nanpercentile(d[fr], [5, 50, 95]).round(1).tolist() if fr.any() else '-'))
    big = np.nonzero(m & (np.abs(d) > 100))[0]
    for j in big[np.linspace(0, len(big) - 1, min(5, len(big))).astype(int)] if len(big) else []:
        print('  t=%d gate %.6g/%.6g (возр %d мс) bybit %.6g/%.6g (возр %d мс) d=%.0f бп' % (ts[j], gb[j], ga[j], ageg[j], bb[j], ba[j], ageb[j], d[j]))
if __name__ == '__main__':
    days(*sys.argv[2:5]) if sys.argv[1] == 'days' else tape(sys.argv[2], sys.argv[3])
