#!/usr/bin/env python3
"""TK-476 гейт правил: свечи 1 м Bybit из дневного архива сделок public.bybit.com против REST kline на одних сутках.
Правила архива: c — цена последней сделки минуты (по timestamp, при равенстве — порядок строк), h/l — max/min цен сделок,
v — сумма size, quote_v — сумма size·price; o — вариант A: цена первой сделки; вариант B: c последней сделки до минуты
(на начало суток — первая сделка суток); h/l в варианте B — с учётом o. Печатает долю совпадений по столбцам для A и B.
Использование: tk476_bybit_gate.py ГГГГ-ММ-ДД СИМВОЛ... ; пишет сводку в stdout (по строке на символ)."""
import csv, gzip, io, json, sys, urllib.request, datetime as dt


def get(u):
    with urllib.request.urlopen(u, timeout=120) as r:
        return r.read()


def rest(sym, d0):
    s = int(dt.datetime.strptime(d0, '%Y-%m-%d').replace(tzinfo=dt.timezone.utc).timestamp()) * 1000
    out = {}
    for st in (s, s + 1000 * 60000):
        x = json.loads(get(f'https://api.bybit.com/v5/market/kline?category=linear&symbol={sym}&interval=1'
                           f'&start={st}&end={st + 999 * 60000}&limit=1000'))
        for r in x['result']['list']:
            if s <= int(r[0]) < s + 86400000:
                out[int(r[0])] = tuple(float(v) for v in r[1:7])  # o h l c v turnover
    return out


def arch(sym, d0):
    raw = get(f'https://public.bybit.com/trading/{sym}/{sym}{d0}.csv.gz')
    rd = csv.reader(io.TextIOWrapper(gzip.GzipFile(fileobj=io.BytesIO(raw)), 'ascii'))
    hdr = next(rd)
    ix = {k: i for i, k in enumerate(hdr)}
    rows = [(float(r[ix['timestamp']]), n, float(r[ix['price']]), float(r[ix['size']])) for n, r in enumerate(rd)]
    rows.sort(key=lambda r: (r[0], r[1]))
    m = {}
    for ts, _, px, q in rows:
        k = int(ts // 60) * 60000
        c = m.get(k)
        if c is None:
            m[k] = [px, px, px, px, q, q * px, 1]  # o h l c v qv n
        else:
            c[1] = max(c[1], px); c[2] = min(c[2], px); c[3] = px; c[4] += q; c[5] += q * px; c[6] += 1
    return m, len(raw), len(rows)


def close(a, b):
    return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b)) + 1e-12


def main():
    d0 = sys.argv[1]
    for sym in sys.argv[2:]:
        a, nb, nr = arch(sym, d0)
        r = rest(sym, d0)
        ks = sorted(a)
        prev = None
        b = {}
        for k in ks:
            o_b = prev if prev is not None else a[k][0]
            b[k] = (o_b, max(a[k][1], o_b), min(a[k][2], o_b), a[k][3])
            prev = a[k][3]
        both = [k for k in ks if k in r]
        flat0 = sum(1 for k in r if k not in a)  # минуты REST без сделок в архиве
        flat0_ok = sum(1 for k in r if k not in a and r[k][4] == 0 and r[k][0] == r[k][1] == r[k][2] == r[k][3])
        res = {'A_o': 0, 'A_h': 0, 'A_l': 0, 'c': 0, 'v': 0, 'qv': 0, 'B_o': 0, 'B_h': 0, 'B_l': 0}
        for k in both:
            ro, rh, rl, rc, rv, rq = r[k]
            ao, ah, al, ac, av, aq, _ = a[k]
            bo, bh, bl, _ = b[k]
            res['A_o'] += close(ao, ro); res['A_h'] += close(ah, rh); res['A_l'] += close(al, rl)
            res['B_o'] += close(bo, ro); res['B_h'] += close(bh, rh); res['B_l'] += close(bl, rl)
            res['c'] += close(ac, rc)
            res['v'] += abs(av - rv) <= 1e-9 * max(1.0, av) + 1e-9
            res['qv'] += abs(aq - rq) <= 1e-6 * max(1.0, aq)
        print(json.dumps({'sym': sym, 'day': d0, 'arch_mb': round(nb / 1e6, 2), 'trades': nr, 'min_arch': len(a),
                          'min_rest': len(r), 'common': len(both), 'rest_only': flat0, 'rest_only_flat_v0': flat0_ok, **res},
                         ensure_ascii=False))


main()
