#!/usr/bin/env python3
"""TK-380: BBO фьючерса Gate по секундам из стакана (TK-379): hour-файл = snapshot(set) + make(+|size| на уровень) / take(-|size|); знак size: + бид, - аск.
Запуск: tk380-ob-bbo.py OUTDIR COIN [COIN...]  -> OUTDIR/COIN.csv.gz: sec,bid,ask (после последнего события секунды; пусто=нет стороны)"""
import sys, os, gzip, heapq, glob
OB = '/srv/alpha-sda/tk379-ob/futures_usdt/orderbooks/202610'
def hour(path, out):
    bids = {}; asks = {}; hb = []; ha = []
    def best():
        while hb and bids.get(-hb[0], 0) <= 0: heapq.heappop(hb)
        while ha and asks.get(ha[0], 0) <= 0: heapq.heappop(ha)
        return (-hb[0] if hb else None), (ha[0] if ha else None)
    cur = None
    for line in gzip.open(path, 'rt'):
        t, a, p, s, _, _ = line.split(',')
        ts = int(float(t)); p = float(p); s = float(s)
        if cur is not None and ts != cur:
            b, k = best(); out.append((cur, b, k))
        cur = ts
        d = bids if s > 0 else asks; q = abs(s)
        if a == 'set': d[p] = q
        elif a == 'make': d[p] = d.get(p, 0) + q
        else: d[p] = d.get(p, 0) - q
        if d.get(p, 0) > 0: heapq.heappush(hb, -p) if s > 0 else heapq.heappush(ha, p)
    if cur is not None:
        b, k = best(); out.append((cur, b, k))
if __name__ == '__main__':
    od = sys.argv[1]; os.makedirs(od, exist_ok=True)
    for c in sys.argv[2:]:
        out = []
        for f in sorted(glob.glob(f'{OB}/{c}_USDT-*.csv.gz')): hour(f, out)
        with gzip.open(f'{od}/{c}.csv.gz', 'wt') as w:
            for t, b, k in out: w.write(f"{t},{'' if b is None else b},{'' if k is None else k}\n")
