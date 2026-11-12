#!/usr/bin/env python3
"""TK-364 шаг 3: псевдотест DEX -> Bybit (пороги Судьи, reviews/tk364-xcorr-2026-10-23.md).
usage: tk364-pseudotest.py OUT_DIR [P]   (запуск на calc; данные /data/tk0364/usd, тики Bybit с public.bybit.com)
Событие: ход price_usd пула >= X бп за T (X 30/100; T свопа/10 с/60 с), <=1 событие на монету за 60 с; вход t = ts блока + 1 с.
Отклик: ход Bybit (перп, иначе спот) в сторону DEX на +1/10/60/600 с; контроль: x10 случайных моментов той же монеты и часа."""
import sys, os, csv, gzip, json, math, random, bisect, array, collections, urllib.request, multiprocessing as mp, statistics
OUT = sys.argv[1]; P = int(sys.argv[2]) if len(sys.argv) > 2 else 6
ROOT = '/data/tk0364/usd'; D0 = 1792022400; DAYS = list(range(15, 22))
XS = (30, 100); TS = (0, 10, 60); HS = (1, 10, 60, 600); NCTL = 10; MINSW = 50
CACHE = '/data/tk0364/pt/by'; os.makedirs(CACHE, exist_ok=True); os.makedirs(OUT, exist_ok=True)

def load_symbols():
    perp, spot = {}, {}
    for r in csv.reader(open('/data/tk0364/bybit-ticks.csv', encoding='utf-8-sig'), delimiter=';'):
        if r[1] == 'symbol' or not r[1].endswith('USDT'): continue
        base = r[1][:-4]; turn = float(r[6] or 0)
        (spot if r[0] == 'спот' else perp)[base] = (r[1], turn)
    return perp, spot

def bybit_sym(base, perp, spot):
    """Bybit-символ монеты; перп, иначе спот; префикс 1000/10000/1000000 перпа."""
    if base in perp: return perp[base][0], 'perp', 1
    for m in (1000, 10000, 1000000, 100000, 10):
        k = f'{m}{base}'
        if k in perp: return perp[k][0], 'perp', m
    if base in spot: return spot[base][0], 'spot', 1
    return None

def day_series(sym, kind, d):
    """1-с ряд последней цены за сутки d (array 86400, NaN до первой сделки)."""
    f = f'{CACHE}/{sym}-{kind}-{d}.bin'
    if os.path.exists(f):
        a = array.array('d'); a.frombytes(open(f, 'rb').read()); return a
    url = (f'https://public.bybit.com/trading/{sym}/{sym}2026-10-{d:02d}.csv.gz' if kind == 'perp'
           else f'https://public.bybit.com/spot/{sym}/{sym}_2026-10-{d:02d}.csv.gz')
    gz = f'{CACHE}/{sym}-{kind}-{d}.gz'
    for att in range(3):
        try:
            urllib.request.urlretrieve(url, gz); break
        except Exception as e:
            if att == 2: return None
    a = array.array('d', [float('nan')]) * 86400
    t0 = D0 + (d - 15) * 86400
    with gzip.open(gz, 'rt') as fh:
        h = fh.readline().strip().split(','); ti, pi = h.index('timestamp'), h.index('price')
        for line in fh:
            s = line.split(','); t = float(s[ti])
            if t > 1e11: t /= 1000
            i = int(t - t0)
            if 0 <= i < 86400: a[i] = float(s[pi])
    os.remove(gz); open(f, 'wb').write(a.tobytes()); return a

def by_series(sym, kind):
    """ряд на всё окно 7 сут, ffill; None если нет суток."""
    out = array.array('d')
    last = float('nan')
    for d in DAYS:
        a = day_series(sym, kind, d)
        if a is None: return None
        for v in a:
            if v == v: last = v
            out.append(last)
    return out

def work(job):
    dirname, net, group, bs = job
    base = dirname.split('-')[0]
    try:
        sym, kind, mult = bs
        by = by_series(sym, kind)
        if by is None: return dict(coin=dirname, status='no_bybit_days')
        # свопы по пулам
        pools = collections.defaultdict(list)
        for d in DAYS:
            p = f'{ROOT}/{net}/{dirname}/2026-10-{d:02d}.csv'
            if not os.path.exists(p): continue
            for r in csv.DictReader(open(p)):
                if r['price_usd']: pools[r['pool']].append((int(r['ts']), int(r['logidx']), float(r['price_usd'])))
        N = len(by)
        res = []
        rnd = random.Random(hash(dirname) & 0xffffff)
        evs_all = []
        status = 'ok'
        for pool, rows in pools.items():
            rows.sort()
            perday = collections.Counter((r[0] - D0) // 86400 for r in rows)
            okdays = {d for d, c in perday.items() if c >= MINSW}
            if not okdays: continue
            # проверка цены токена: медиана DEX / медиана Bybit * mult около 1
            dm = statistics.median(r[2] for r in rows)
            bm = statistics.median(by[i] for i in range(0, N, 600) if by[i] == by[i]) / mult
            ratio = dm / bm if bm else 0
            if not (0.8 < ratio < 1.25): status = f'price_mismatch({ratio:.3g})'; continue
            ts = [r[0] for r in rows]; px = [r[2] for r in rows]
            for x in XS:
                for T in TS:
                    last_ev = -10**9
                    for i in range(1, len(rows)):
                        t = ts[i]
                        if (t - D0) // 86400 not in okdays: continue
                        if T == 0: j = i - 1
                        else:
                            j = bisect.bisect_right(ts, t - T) - 1
                            if j < 0: continue
                        mv = math.log(px[i] / px[j]) * 1e4
                        if abs(mv) < x: continue
                        if t - last_ev < 60: continue
                        last_ev = t
                        evs_all.append((x, T, t, mv, (t - D0) // 86400))
        # <=1 событие на монету за 60 с (по клетке): оставить первое
        evs_all.sort(key=lambda e: (e[0], e[1], e[2]))
        cells = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0.0, 0.0, 0.0, 0.0, 0]))
        lastc = {}
        for x, T, t, mv, dd in evs_all:
            k = (x, T)
            if t - lastc.get(k, -10**9) < 60: continue
            lastc[k] = t
            sg = 1 if mv > 0 else -1
            te = t + 1 - D0
            pre_w = max(T, 10)
            if te - pre_w < 0 or te + max(HS) >= N: continue
            pe = by[te]
            if pe != pe: continue
            pre = sg * math.log(pe / by[te - pre_w]) * 1e4 if by[te - pre_w] == by[te - pre_w] else 0.0
            follow = 1 if pre >= 0.5 * abs(mv) else 0
            hr = (te // 3600) * 3600
            for h in HS:
                ev = sg * math.log(by[te + h] / pe) * 1e4
                cs = 0.0; cn = 0
                for _ in range(NCTL):
                    tc = hr + rnd.randrange(3600)
                    if tc + 1 + h >= N or tc + 1 < 0: continue
                    p0 = by[tc + 1]; p1 = by[tc + 1 + h]
                    if p0 == p0 and p1 == p1: cs += sg * math.log(p1 / p0) * 1e4; cn += 1
                if not cn: continue
                c = cells[k][(h, dd)]
                c[0] += 1; c[1] += ev; c[2] += cs / cn; c[3] += pre
                c[4] += ev - cs / cn; c[5] += follow
        return dict(coin=dirname, status=status, net=net, group=group, sym=sym, kind=kind,
                    cells={f'{k[0]}|{k[1]}|{h}|{dd}': v for k, hh in cells.items() for (h, dd), v in hh.items()},
                    nev=len(evs_all))
    except Exception as e:
        return dict(coin=dirname, status=f'error:{type(e).__name__}:{e}')

def main():
    perp, spot = load_symbols()
    groups = json.load(open('/data/tk0364/pt/groups.json'))
    jobs = []
    for net in ('ethereum', 'base', 'bsc'):
        for dn in sorted(os.listdir(f'{ROOT}/{net}')):
            base = dn.split('-')[0]
            bs = bybit_sym(base, perp, spot)
            if not bs: continue
            g = 'calib' if base in groups['calib'] else 'gold' if base in groups['gold'] else 'high' if base in groups['high'] else 'mid'
            jobs.append((dn, net, g, bs))
    # покрытие суток по сети (число пулов с данными / максимум)
    print('jobs', len(jobs), flush=True)
    with mp.Pool(P) as pool:
        out = []
        for i, r in enumerate(pool.imap_unordered(work, jobs), 1):
            out.append(r)
            if i % 10 == 0: print(i, len(jobs), flush=True)
    json.dump(out, open(f'{OUT}/raw.json', 'w'))
    open(f'{OUT}/DONE.json', 'w').write(json.dumps({'jobs': len(jobs), 'ok': sum(1 for r in out if r['status'] == 'ok')}))

if __name__ == '__main__': main()
