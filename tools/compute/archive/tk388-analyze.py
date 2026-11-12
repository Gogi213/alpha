#!/usr/bin/env python3
"""TK-388 / P-27 §3 часть A: вердикт по заранее заданной ячейке. Вход: каталог с episodes-T*.csv либо episodes.csv. Печатает json."""
import csv, glob, sys, random, statistics, json, collections
def load(d, T):
    f = glob.glob(f'{d}/episodes-T{T}.csv') or glob.glob(f'{d}/episodes.csv')
    return list(csv.DictReader(open(f[0])))
def cell(rows, T, form='a', ex='half', H='2ч'):
    return [r for r in rows if float(r['T']) == T and r['form'] == form and r['ex'] == ex and r['H'] == H]
def stat(r, shift=0.0, seed='388'):
    if not r: return dict(n=0)
    net = [float(x['net']) + shift for x in r]; ct = [float(x['ctrl']) + shift for x in r if x['ctrl'] != '']
    byd = collections.defaultdict(list)
    for x, v in zip(r, net): byd[x['day']].append(v)
    days = list(byd.values()); rng = random.Random(seed); bs = []
    for _ in range(5000):
        s = [v for d in (rng.choice(days) for _ in days) for v in d]; bs.append(sum(s) / len(s))
    bs.sort()
    wk = collections.defaultdict(list)
    for x, v in zip(r, net): wk[min(int(x['dayidx']) // 7, 5)].append(v)
    return dict(n=len(net), effN=len(days), net=round(sum(net) / len(net), 1), ctrl=round(sum(ct) / len(ct), 1) if ct else None, lo2p5=round(bs[125], 1), lo5=round(bs[250], 1),
                weeks={w: (len(v), round(sum(v) / len(v), 1)) for w, v in sorted(wk.items())}, conv=round(sum(int(x['conv']) for x in r) / len(r), 2),
                gross=round(statistics.mean(float(x['pnl_gross']) for x in r), 1))
if __name__ == '__main__':
    out = {}
    for name, d, T in (a.split(':') for a in sys.argv[1:]):
        out[name] = stat(cell(load(d, T), float(T)))
    print(json.dumps(out, ensure_ascii=False, indent=1))
