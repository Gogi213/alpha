#!/usr/bin/env python3
"""TK-394: BBO фьючерса Gate по секундам из стакана TK-379, исправленная сборка (tk380-ob-bbo.py держал float-остатки
после take -> призрачные уровни -> бид>аск). hour-файл = snapshot(set) + make(+|size|) / take(-|size|); знак size: + бид, - аск.
Размер округляется до 9 знаков, уровень <=0 удаляется. Состояние сбрасывается на каждом часовом файле (в нём свой снимок).
Запуск: tk394-gate-bbo.py OUTDIR COIN [COIN...] [--days 2026-10-16,...] -> OUTDIR/COIN.csv.gz: sec,bid,ask (после последнего события секунды)
Проверка (--check): доля секунд бид>=аск и доля сделок Gate фьюча в [бид; аск] (BBO последней строки до секунды сделки; и в [мин бид; макс аск] окна секунды сделки)."""
import sys, os, gzip, heapq, glob
OB = '/srv/alpha-sda/tk379-ob/futures_usdt/orderbooks/202610'
TR = '/srv/alpha-sda/tk372-gate/raw/fut'
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
        isb = s > 0; d = bids if isb else asks; q = abs(s)
        if a == 'set': d[p] = q
        elif a == 'make': d[p] = round(d.get(p, 0) + q, 9)
        else: d[p] = round(d.get(p, 0) - q, 9)
        if d[p] <= 0: del d[p]
        else: heapq.heappush(hb, -p) if isb else heapq.heappush(ha, p)
    if cur is not None:
        b, k = best(); out.append((cur, b, k))
def check(c, out, day):
    import bisect
    secs = [r[0] for r in out]; n = x = 0
    for r in out:
        n += 1; x += (r[1] is not None and r[2] is not None and r[1] >= r[2])
    tn = ti = tw = 0
    for line in gzip.open(f'{TR}/{c}_USDT/{day}.csv.gz', 'rt'):
        f = line.split(',')
        if f[0] == 'id': continue
        ts = int(float(f[2])); pr = float(f[5])
        j = bisect.bisect_left(secs, ts) - 1  # последняя строка строго до секунды сделки
        if j < 0 or out[j][1] is None or out[j][2] is None: continue
        tn += 1; ti += (out[j][1] <= pr <= out[j][2])
        w = [r for r in out[max(j, 0):bisect.bisect_right(secs, ts)] if r[1] is not None and r[2] is not None]
        tw += (min(r[1] for r in w) <= pr <= max(r[2] for r in w))
    return n, x, tn, ti, tw
if __name__ == '__main__':
    args = sys.argv[1:]; days = None; chk = '--check' in args
    if '--days' in args:
        i = args.index('--days'); days = args[i + 1].split(','); del args[i:i + 2]
    args = [a for a in args if a != '--check']
    od = args[0]; os.makedirs(od, exist_ok=True)
    for c in args[1:]:
        out = []
        for f in sorted(glob.glob(f'{OB}/{c}_USDT-*.csv.gz')):
            if days and not any(f'-{d.replace("-", "")}' in f for d in days): continue
            hour(f, out)
        if not days:
            with gzip.open(f'{od}/{c}.csv.gz', 'wt') as w:
                for t, b, k in out: w.write(f"{t},{'' if b is None else b},{'' if k is None else k}\n")
        if chk and days:
            day = days[0]; lo = int(__import__('calendar').timegm(__import__('time').strptime(day, '%Y-%m-%d')))
            o = [r for r in out if lo <= r[0] < lo + 86400]
            n, x, tn, ti, tw = check(c, o, day); print(c, day, 'secs', n, 'crossed', x, f'{x/max(n,1):.4f}', 'trades', tn, 'in', ti, f'{ti/max(tn,1):.4f}', 'in_window', tw, f'{tw/max(tn,1):.4f}', flush=True)
