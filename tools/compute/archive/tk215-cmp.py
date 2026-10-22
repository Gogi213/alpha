#!/usr/bin/env python3
"""TK-215: сверка пула (прогон A) с прямым счётом (прогон B) на calc: /data/tk214/out{A,B}; отчёт /data/tk214/cmp/report.txt."""
import csv, os, json, hashlib, subprocess, glob

R = '/data/tk214'
DAYS = ['2026-01-02', '2026-01-03']
SETS = ['t-bid-btc4h-q1', 'g87-fresh', 'e106-pop', 'g10n12q90-2026-01', 'g10n4q50-2026-01']
F = 'ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800'
T = '/data/tk083/tools'
MO = '2026-01'


def rd(p):
    with open(p, encoding='utf-8') as f:
        return list(csv.DictReader(l for l in f if not l.startswith('#')))


def base(run, d, s):
    return f'{R}/out{run}/{d}/out/{d}/{s}'


out = []


def P(*a):
    out.append(' '.join(str(x) for x in a))
    print(out[-1], flush=True)


os.makedirs(f'{R}/cmp', exist_ok=True)
A = {}
for d in DAYS:
    for kind in ('rounds', 'signals'):
        A[d, kind] = {(r['symbol'], r['form'], r['t0_ns']): {k: v for k, v in r.items() if k != 'signal_index'}
                      for r in rd(base('A', d, 'pool') + f'/{kind}.csv')}
P('(1) строки B = строки A по (symbol,form,t0_ns); сутки суммированы')
for s in SETS:
    for kind in ('rounds', 'signals'):
        eq = ne = miss = 0
        ex = []
        for d in DAYS:
            for r in rd(base('B', d, s) + f'/{kind}.csv'):
                k = (r['symbol'], r['form'], r['t0_ns'])
                b = {x: v for x, v in r.items() if x != 'signal_index'}
                a = A[d, kind].get(k)
                if a is None:
                    miss += 1
                    if len(ex) < 3:
                        ex.append(('нет в A', k))
                elif a == b:
                    eq += 1
                else:
                    ne += 1
                    if len(ex) < 3:
                        ex.append(('разл', k, [(c, a.get(c), b[c]) for c in b if a.get(c) != b[c]][:3]))
        P(f'  {s} {kind}: равных {eq}, неравных {ne}, нет в A {miss}', ex if ex else '')
P('(3) колонки фильтра в signals.csv пула')
hdr = list(rd(base('A', DAYS[0], 'pool') + '/signals.csv')[0].keys())
for c in ['age', 'btc4h', 'btc4h_max', 'btc12h', 'btc12h_max', 'r1_born_shift_cbps', 'born_shift_cbps', 'side', 'dir']:
    P('  ', c, 'есть' if c in hdr else 'НЕТ')


def tree(name, run, sets):
    for d in DAYS:
        for s, tgt in sets:
            p = f'{R}/cmp/{name}/{MO}/{d}'
            os.makedirs(p, exist_ok=True)
            l = f'{p}/{tgt}'
            os.makedirs(l, exist_ok=True)  # os.walk не идёт по симлинкам каталогов — линкуем файлы
            for fn in ('rounds.csv', 'signals.csv'):
                if not os.path.lexists(f'{l}/{fn}'):
                    os.symlink(f'{base(run, d, s)}/{fn}', f'{l}/{fn}')
    return f'{R}/cmp/{name}/{MO}'


def norm(path):
    j = json.load(open(path))
    v = next(iter(j.values()))
    return hashlib.sha256(json.dumps(v, sort_keys=True).encode()).hexdigest()[:16]


P('(2) busy-replay -> psim: closes A|t0(B) против B')
ta = tree('v2A', 'A', [('pool', 'pool')])
for s in SETS:
    if not any(rd(base('B', d, s) + '/signals.csv') for d in DAYS):
        P(f'  {s}: у B 0 сигналов за обе сутки — сверять нечего')
        continue
    keep = f'{R}/cmp/keep-{s}.csv'
    with open(keep, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['symbol', 't0_ns', 'price_tick', 'form'])
        for d in DAYS:
            for r in rd(base('B', d, s) + '/signals.csv'):
                w.writerow([r['symbol'], r['t0_ns'], r['price_tick'], r['form']])
    oa = f'{R}/cmp/ba3-{s}/{MO}'
    ob = f'{R}/cmp/bb3-{s}/{MO}'
    tb = tree('v2B-' + s, 'B', [(s, s)])
    for o in (oa, ob):
        pass
    subprocess.run(['python3', f'{T}/busy-replay.py', ta, oa, '--keep', keep, '--sets', 'pool'], check=True, capture_output=True, text=True)
    subprocess.run(['python3', f'{T}/busy-replay.py', tb, ob, '--sets', s], check=True, capture_output=True, text=True)
    res = {}
    for tag, od, sd in (('A_B', oa, 'pool'), ('B', ob, s)):
        cj = f'{R}/cmp/closes-{tag}-{s}.json'
        rc = subprocess.run(['python3', f'{T}/portfolio-sim.py', '--epoch', f'{MO}={os.path.dirname(od)}:{MO}',
                             '--variant', f'{sd}@{F}={sd}/{F}', '--variant', f'{sd}@{F}-pyfresh10={sd}/{F}-pyfresh10',
                             '--deposit-usd', '2500', '--position-usd', '500', '--max-pos', '0', '--closes-out', cj],
                            capture_output=True, text=True)
        res[tag] = norm(cj) if rc.returncode == 0 else 'FAIL ' + rc.stderr[-300:]
    ra = sum(len(rd(p)) for p in glob.glob(f'{oa}/*/pool/rounds.csv'))
    rb = sum(len(rd(p)) for p in glob.glob(f'{ob}/*/{s}/rounds.csv'))
    P(f'  {s}: closes A|B={res["A_B"]} B={res["B"]} {"РАВНЫ" if res["A_B"] == res["B"] else "РАЗНЫ"}; rounds после replay A|B {ra}, B {rb}')
open(f'{R}/cmp/report.txt', 'w').write('\n'.join(out) + '\n')
