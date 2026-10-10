#!/usr/bin/env python3
"""Гейт В-177 по окнам минута/час: корзины, p99, признаки а/б/в, вердикт по суткам.
Вход: CSV из `lob verify --windows-out` (symbol,part,kind,id,n,u,v,vw,max_run), один или много.
Выход: <out>-days.csv (сутки: gate=ok|fail, причина), <out>-windows.csv (порченые окна), <out>-bounds.json."""
import bisect, csv, glob, json, math, re, sys
from collections import defaultdict

NB = 20
GRID = (1e-2, 1e-3, 1e-4, 1e-5)
CAL = defaultdict(lambda: {x: defaultdict(int) for x in 'abc'})
P99 = 0.99

def binom_tail(v, n, p):
    if v <= 0: return 1.0
    if p <= 0: return 0.0
    if p >= 1: return 1.0
    lp, lq = math.log(p), math.log1p(-p)
    lg = math.lgamma(n + 1)
    best, terms = None, []
    for k in range(v, n + 1):
        t = lg - math.lgamma(k + 1) - math.lgamma(n - k + 1) + k * lp + (n - k) * lq
        terms.append(t)
        if best is None: best = t
        if t < best - 40: break
    m = max(terms)
    return min(1.0, math.exp(m) * sum(math.exp(t - m) for t in terms))

def bb_tail(v, n, p, rho):
    """P(X>=v) бета-биномиальный; rho=0 -> биномиальный."""
    if v <= 0: return 1.0
    if rho <= 1e-9 or p <= 0 or p >= 1: return binom_tail(v, n, p)
    a = p * (1 - rho) / rho; b = (1 - p) * (1 - rho) / rho
    lb = lambda x, y: math.lgamma(x) + math.lgamma(y) - math.lgamma(x + y)
    base = math.lgamma(n + 1) - lb(a, b)
    terms = []; best = None
    for k in range(v, n + 1):
        t = base - math.lgamma(k + 1) - math.lgamma(n - k + 1) + lb(k + a, n - k + b)
        terms.append(t)
        if best is None: best = t
        if t < best - 40: break
    m = max(terms)
    return min(1.0, math.exp(m) * sum(math.exp(t - m) for t in terms))

def rho_from_sums(k, N, X, Q, M2):
    """Сверхрассеяние методом моментов по суммам корзины: k окон, N=Σn, X=Σx, Q=Σx²/n, M2=Σn²; [0, 0.999]."""
    if k < 2 or N <= 0: return 0.0, (X / N if N > 0 else 0.0)
    p = X / N
    if p <= 0 or p >= 1: return 0.0, p
    S = Q - 2 * p * X + p * p * N
    num = S / (p * (1 - p)) - (k - 1)
    den = N - k - M2 / N + 1
    return (min(0.999, max(0.0, num / den)) if den > 0 else 0.0), p

def run_tail(ne, ve, m):
    """Бонферрони: P(серия нарушений >= m | ne событий, ve нарушений, случайный порядок) <= (ne-m+1)*prod_{i<m}(ve-i)/(ne-i)."""
    if m < 2 or ve < m or ne < m: return 1.0
    l = math.log(ne - m + 1) + sum(math.log(ve - i) - math.log(ne - i) for i in range(m))
    return min(1.0, math.exp(l))

def pois_q(mu, q):
    """Квантиль Пуассона: наименьшее k с P(X<=k) >= q (логарифмы — без потери при больших mu)."""
    if mu <= 0: return 0
    c, k = 0.0, 0
    while True:
        c += math.exp(-mu + k * math.log(mu) - math.lgamma(k + 1))
        if c >= q or k > 10 * mu + 1000: return k
        k += 1

def quantile(sorted_xs, q):
    if not sorted_xs: return 0.0
    i = min(len(sorted_xs) - 1, max(0, math.ceil(q * len(sorted_xs)) - 1))
    return sorted_xs[i]

def main(out, base_pats, apply_pats):
    def load(pats):
        rows = []
        for pat in pats:
            for f in sorted(glob.glob(pat)):
                with open(f, newline='', encoding='utf-8') as fh:
                    for r in csv.DictReader(fh):
                        rows.append((r['symbol'], r['part'], r['kind'], int(r['id']), int(r['n']), int(r['u']),
                                     int(r['v']), int(r['vw']), int(r['vws']), int(r['max_run']),
                                     int(r['ne']), int(r['ve']), int(r['vwe']), int(r['max_run_e'])))
        return rows
    base, extra = load(base_pats), load(apply_pats)
    ratio = lambda r: r[4] / r[5] if r[5] else float('inf')
    bounds, flagged = {}, []
    for kind in sorted({r[2] for r in base}):
        rs = [r for r in base if r[2] == kind]
        rr = sorted(ratio(r) for r in rs)
        edges = [rr[min(len(rr) - 1, (i * len(rr)) // NB)] for i in range(1, NB)]
        def bucket(r):
            x = ratio(r); b = 0
            while b < NB - 1 and x >= edges[b]: b += 1
            return b
        alpha = 1.0 / len(rs)
        B = defaultdict(lambda: dict(ne=0, ve=0, kb=0, Xb=0, Qb=0.0, k=0, X=0, Q=0.0, M2=0, xv=[], xw=[]))
        for r in rs:
            a = B[bucket(r)]
            a['ne'] += r[10]; a['ve'] += r[11]
            if r[10]:
                a['kb'] += 1; a['Xb'] += r[11]; a['Qb'] += r[11] ** 2 / r[10]
                a['k'] += 1; a['X'] += r[12]; a['Q'] += r[12] ** 2 / r[10]; a['M2'] += r[10] ** 2
                a['xv'].append(r[11] / r[10]); a['xw'].append(r[12] / r[10])
        for a in B.values(): a['xv'].sort(); a['xw'].sort()
        def q99(xs, own):
            """p99 порядковой статистикой; own — значение своего окна, которое надо исключить (None — не исключать)."""
            m = len(xs) - (own is not None)
            if m <= 0: return 0.0
            j = min(m - 1, max(0, math.ceil(P99 * m) - 1))
            if own is None: return xs[j]
            pos = bisect.bisect_left(xs, own)
            return xs[j] if j < pos else xs[j + 1]
        def calib_add(kind_, ta, tb, tc):
            c = CAL[kind_]
            for name, t in (('a', ta), ('b', tb), ('c', tc)):
                for g in GRID:
                    if t <= g: c[name][g] += 1
        def judge(r, loo):
            sym, part, k_, wid, n, u, v, vw, vws, mr, ne, ve, vwe, mre = r
            a = B[bucket(r)]
            nne, vve = a['ne'], a['ve']
            kk, X, Q, M2 = a['k'], a['X'], a['Q'], a['M2']
            kb, Xb, Qb = a['kb'], a['Xb'], a['Qb']
            if loo:
                nne -= ne; vve -= ve
                if ne:
                    kk -= 1; X -= vwe; Q -= vwe ** 2 / ne; M2 -= ne ** 2
                    kb -= 1; Xb -= ve; Qb -= ve ** 2 / ne
            pe = vve / nne if nne else 0.0
            rho, pw = rho_from_sums(kk, nne, X, Q, M2)
            rhob, pb = rho_from_sums(kb, nne, Xb, Qb, M2)
            p99b = q99(a['xv'], ve / ne if loo and ne else None)
            p99w = q99(a['xw'], vwe / ne if loo and ne else None)
            why = []
            ta = run_tail(ne, ve, mre)
            tb = bb_tail(ve, ne, pb, rhob) if ve > 0 and ne and ve / ne > pb else 1.0
            tc = bb_tail(vwe, ne, pw, rho) if vwe > 0 and ne and vwe / ne > pw else 1.0
            if ta < alpha: why.append('a')
            if ve > 0 and ne and ve / ne > p99b and tb < alpha: why.append('b')
            if vwe > 0 and ne and vwe / ne > p99w and tc < alpha: why.append('c')
            if loo: calib_add(kind, ta, tb, tc)
            return ''.join(why)
        for r in rs:
            w = judge(r, True)
            if w: flagged.append(r + (w,))
        for r in extra:
            if r[2] == kind:
                w = judge(r, False)
                if w: flagged.append(r + (w,))
        nflag = sum(1 for f in flagged if f[2] == kind)
        bounds[kind] = dict(alpha=alpha, windows=len(rs), flagged=nflag,
            calibration={x: {f'{g:g}': dict(observed=CAL[kind][x][g], expected=len(rs) * g) for g in GRID} for x in 'abc'},
            edges=edges, buckets={
            str(b): dict(pe=a['ve'] / a['ne'] if a['ne'] else 0.0,
                         rho_b=rho_from_sums(a['kb'], a['ne'], a['Xb'], a['Qb'], a['M2'])[0],
                         rho=rho_from_sums(a['k'], a['ne'], a['X'], a['Q'], a['M2'])[0],
                         pw=a['X'] / a['ne'] if a['ne'] else 0.0,
                         p99b=q99(a['xv'], None), p99w=q99(a['xw'], None)) for b, a in sorted(B.items())})
    rows = base + extra
    days = defaultdict(lambda: [0, 0, 0, 0, []])
    for r in rows:
        d = days[(r[0], r[1])]
        if r[2] == 'h': d[0] += r[4]; d[1] += r[6]; d[2] += r[7]; d[3] += r[8]
    for f in flagged: days[(f[0], f[1])][4].append(f)
    only_c_v0 = 0
    with open(out + '-days.csv', 'w', newline='') as fh:
        w = csv.writer(fh); w.writerow(['symbol', 'part', 'trades', 'v', 'vw', 'vws', 'gate', 'gate_ab', 'flagged', 'first_window', 'signs'])
        for (s_, p_), (n, v, vw, vws, fl) in sorted(days.items()):
            first = min(fl, key=lambda x: (x[2], x[3])) if fl else None
            signs = ''.join(sorted({c for x in fl for c in x[14]}))
            if signs == 'c' and v == 0: only_c_v0 += 1
            w.writerow([s_, p_, n, v, vw, vws, 'fail' if fl else 'ok',
                        'fail' if any(set(x[14]) & {'a', 'b'} for x in fl) else 'ok', len(fl),
                        f'{first[2]}:{first[3]}' if first else '', signs])
    with open(out + '-windows.csv', 'w', newline='') as fh:
        w = csv.writer(fh); w.writerow(['symbol', 'part', 'kind', 'id', 'n', 'u', 'v', 'vw', 'vws', 'max_run', 'ne', 've', 'vwe', 'max_run_e', 'signs'])
        w.writerows(flagged)
    json.dump(bounds, open(out + '-bounds.json', 'w'), indent=1)
    nd = len(days); nf = sum(1 for d in days.values() if d[4])
    for kind, b in bounds.items():
        print(f"calib {kind} windows={b['windows']} flagged={b['flagged']} alpha={b['alpha']:.2e}")
        for x in 'abc':
            print('  ', x, ' '.join(f"t={g}: obs={v['observed']} exp={v['expected']:.0f} q999={pois_q(v['expected'], 0.999)}" for g, v in b['calibration'][x].items()))
    print(f'windows={len(rows)} days={nd} fail={nf} flagged_windows={len(flagged)} days_only_c_with_v0={only_c_v0}')

if __name__ == '__main__':
    # tk044-gate.py <out> <база.csv...> [--apply <вброшенные.csv...>]: пороги — только из базы (окно базы судится без себя);
    # окна после --apply судятся против этих замороженных порогов и в них не входят
    a = sys.argv[2:]
    if '--apply' in a:
        k = a.index('--apply'); main(sys.argv[1], a[:k], a[k + 1:])
    else:
        main(sys.argv[1], a, [])
