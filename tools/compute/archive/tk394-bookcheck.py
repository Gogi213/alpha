#!/usr/bin/env python3
"""TK-394 возврат 4: сверка сборки книги Gate фьюча со снимком начала следующего часа.
Запуск: tk394-bookcheck.py OUT.json COIN:DAY ... ; по каждому часу h: книга после всех событий h vs set-снимок h+1.
Метрики: доля уровней топ-20 каждой стороны (по снимку) с тем же размером (отн. 1e-6), BBO совпал, число уровней сверх снимка в топ-зоне."""
import sys, os, gzip, json, glob
OB = '/srv/alpha-sda/tk379-ob/futures_usdt/orderbooks/202610'
def load(path, mode):
    bids = {}; asks = {}; snap_b = {}; snap_a = {}
    for line in gzip.open(path, 'rt'):
        t, a, p, s, _, _ = line.split(',')
        p = float(p); s = float(s); isb = s > 0; q = abs(s)
        if a == 'set':
            (snap_b if isb else snap_a)[p] = q
            (bids if isb else asks)[p] = q
        else:
            d = bids if isb else asks
            if mode == 'delta':
                d[p] = round(d.get(p, 0) + (q if a == 'make' else -q), 9)
            elif mode == 'setlike':  # make/take = новый остаток
                d[p] = q
            if d.get(p, 0) <= 0: d.pop(p, None)
    return bids, asks, snap_b, snap_a
def cmp(book, snap, side, k=20):
    ps = sorted(snap, reverse=(side == 'b'))[:k]
    same = sum(1 for p in ps if p in book and abs(book[p] - snap[p]) <= 1e-6 * max(snap[p], 1e-9))
    inbook = sum(1 for p in ps if p in book)
    # лишние уровни книги внутри диапазона топ-20 снимка
    lo, hi = min(ps), max(ps)
    extra = sum(1 for p in book if lo <= p <= hi and p not in snap)
    return len(ps), same, inbook, extra
res = []
for spec in sys.argv[2:]:
    coin, day = spec.split(':')
    files = sorted(glob.glob(f'{OB}/{coin}_USDT-{day.replace("-", "")}*.csv.gz'))
    nxt = sorted(glob.glob(f'{OB}/{coin}_USDT-*.csv.gz'))
    for i, f in enumerate(files[:-1]):
        g = files[i + 1]
        bk, ak, _, _ = load(f, 'delta')
        _, _, sb, sa = load(g, 'none')
        r = {'coin': coin, 'file': os.path.basename(f), 'snap_levels': [len(sb), len(sa)]}
        for side, book, snap in (('b', bk, sb), ('a', ak, sa)):
            n, same, inb, extra = cmp(book, snap, side)
            r[side] = {'top': n, 'same': same, 'in_book': inb, 'extra_in_range': extra}
        bb = max(bk) if bk else None; ba = min(ak) if ak else None
        r['bbo_book'] = [bb, ba]; r['bbo_snap'] = [max(sb), min(sa)]
        r['bbo_same'] = bool(bb == max(sb) and ba == min(sa))
        res.append(r); print(coin, r['file'], 'b', r['b']['same'], '/', r['b']['top'], 'a', r['a']['same'], '/', r['a']['top'], 'bbo_same', r['bbo_same'], flush=True)
json.dump(res, open(sys.argv[1], 'w'))
