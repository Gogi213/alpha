#!/usr/bin/env python3
"""TK-374: банк P-25 в паре DEX <-> Gate, монеты Gate без Bybit. Запуск на calc: tk374-bank.py OUT_DIR [P]
Данные: DEX-слой /data/tk0374/usd/<сеть>/<МОНЕТА-хвост пула>/<день>.csv (ts,price_usd), Gate /srv/alpha-sda/tk372-gate/raw/{fut,spot}/<МОНЕТА>_USDT.
Прямой тест (dir=fwd): событие DEX (ход price_usd пула >= X бп за T; X 30/100; T свопа/10 с/60 с; <=1 на монету за 60 с), вход t+1 с,
  отклик: ход Gate (фьюч, иначе спот) в сторону DEX на +1/10/60/600 с; контроль x10 случайных моментов той же монеты и часа.
Обратный (dir=rev): событие Gate (ход цены >= X бп за T; X 30/100/q95 монеты; T 10/60 с; <=1 на монету за 60 с), вход t+1 с,
  отклик: ход цены главного пула монеты (последний своп <= t+h) в сторону Gate; тот же контроль.
Пул на сутки идёт в счёт при >= MINSW свопах в сутках; цена токена DEX/Gate по медианам в 0,8-1,25."""
import sys, os, csv, gzip, json, math, random, bisect, array, collections, statistics, multiprocessing as mp
OUT = sys.argv[1]; P = int(sys.argv[2]) if len(sys.argv) > 2 else 15
ROOT = '/data/tk0374/usd'; GR = '/srv/alpha-sda/tk372-gate/raw'; D0 = 1792022400; DAYS = list(range(15, 22))
XS = (30, 100); TS = (0, 10, 60); HS = (1, 10, 60, 600); NCTL = 10; MINSW = 50; N = 7 * 86400
# вселенная TK-372: монеты Gate без Bybit (454) по рынкам; каталоги raw/ шире (там и общие с Bybit) — берём только список
UNI = collections.defaultdict(set)
for r in csv.DictReader(open('/srv/alpha-sda/tk372-gate/universe.csv', encoding='utf-8-sig')):
    UNI[r['symbol'][:-5]].add('fut' if r['market'] == 'фьюч' else 'spot')
os.makedirs(OUT, exist_ok=True)

def gate_series(coin):
    """1-с ряд последней цены Gate на 7 суток (ffill); фьюч, иначе спот. -> (array, kind) или None."""
    for kind in ('fut', 'spot'):
        d = f'{GR}/{kind}/{coin}_USDT'
        if kind not in UNI.get(coin, ()) or not os.path.isdir(d): continue
        a = array.array('d', [float('nan')]) * N
        got = 0
        for day in DAYS:
            f = f'{d}/2026-10-{day:02d}.csv.gz'
            if not os.path.exists(f): continue
            with gzip.open(f, 'rt') as fh:
                h = fh.readline().rstrip('\n').split(','); ti, pi = h.index('create_time_ms'), h.index('price')
                for line in fh:
                    s = line.split(',')
                    try: i = int(float(s[ti]) / 1000) - D0
                    except ValueError: continue
                    if 0 <= i < N: a[i] = float(s[pi]); got += 1
        if got < 200: continue
        last = float('nan')
        for i in range(N):
            v = a[i]
            if v == v: last = v
            else: a[i] = last
        return a, kind
    return None

def load_pools(net, dirname):
    pools = collections.defaultdict(list)
    for d in DAYS:
        p = f'{ROOT}/{net}/{dirname}/2026-10-{d:02d}.csv'
        if not os.path.exists(p): continue
        for r in csv.DictReader(open(p)):
            if r['price_usd']: pools[r['pool']].append((int(r['ts']), int(r['logidx']), float(r['price_usd'])))
    return pools

def add(cells, k, h, dd, ev, ctl, pre, follow):
    c = cells[k][(h, dd)]
    c[0] += 1; c[1] += ev; c[2] += ctl; c[3] += pre; c[4] += ev - ctl; c[5] += follow

def work(job):
    dirname, net = job
    coin = dirname.split('-')[0]
    try:
        gs = gate_series(coin)
        if gs is None: return dict(coin=dirname, status='no_gate')
        g, kind = gs
        pools = load_pools(net, dirname)
        cells = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0.0, 0.0, 0.0, 0.0, 0]))
        rnd = random.Random(hash(dirname) & 0xffffff)
        status = 'ok'; best = None; nev = [0, 0]
        gm = statistics.median(g[i] for i in range(0, N, 600) if g[i] == g[i]) if any(v == v for v in g) else 0
        for pool, rows in pools.items():
            rows.sort()
            perday = collections.Counter((r[0] - D0) // 86400 for r in rows)
            okdays = {d for d, c in perday.items() if c >= MINSW}
            if not okdays: continue
            ratio = statistics.median(r[2] for r in rows) / gm if gm else 0
            if not (0.8 < ratio < 1.25): status = f'price_mismatch({ratio:.3g})'; continue
            if best is None or len(rows) > len(best[1]): best = (pool, rows, okdays)
            ts = [r[0] for r in rows]; px = [r[2] for r in rows]
            evs = []
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
                        if abs(mv) < x or t - last_ev < 60: continue
                        last_ev = t; evs.append((x, T, t, mv, (t - D0) // 86400))
            for x, T, t, mv, dd in sorted(evs):
                sg = 1 if mv > 0 else -1; te = t + 1 - D0; pre_w = max(T, 10)
                if te - pre_w < 0 or te + max(HS) >= N: continue
                pe = g[te]
                if pe != pe or g[te - pre_w] != g[te - pre_w]: continue
                pre = sg * math.log(pe / g[te - pre_w]) * 1e4
                follow = 1 if pre >= 0.5 * abs(mv) else 0
                hr = (te // 3600) * 3600; nev[0] += 1
                for h in HS:
                    ev = sg * math.log(g[te + h] / pe) * 1e4
                    cs = 0.0; cn = 0
                    for _ in range(NCTL):
                        tc = hr + rnd.randrange(3600)
                        if tc + 1 + h >= N: continue
                        p0 = g[tc + 1]; p1 = g[tc + 1 + h]
                        if p0 == p0 and p1 == p1: cs += sg * math.log(p1 / p0) * 1e4; cn += 1
                    if cn: add(cells, f'fwd|{x}|{T}', h, dd, ev, cs / cn, pre, follow)
        # обратный: события Gate, отклик главного пула
        if best is not None:
            pool, rows, okdays = best
            ts = [r[0] for r in rows]; px = [r[2] for r in rows]
            def dp(t):
                k = bisect.bisect_right(ts, t) - 1
                return px[k] if k >= 0 else None
            ch = [i for i in range(1, N) if g[i] == g[i] and g[i - 1] == g[i - 1] and g[i] != g[i - 1]]
            for T in (10, 60):
                mvs = {i: math.log(g[i] / g[i - T]) * 1e4 for i in ch if i >= T and g[i - T] == g[i - T]}
                a = sorted(abs(v) for v in mvs.values())
                q95 = max(10.0, a[int(0.95 * len(a))]) if len(a) >= 100 else None
                for xl, x in (('30', 30.0), ('100', 100.0), ('q95', q95)):
                    if x is None: continue
                    last_ev = -10**9
                    for i in sorted(mvs):
                        mv = mvs[i]
                        if abs(mv) < x or i - last_ev < 60: continue
                        t = i + D0; dd = i // 86400
                        if dd not in okdays: continue
                        last_ev = i; sg = 1 if mv > 0 else -1; te = t + 1
                        if i + 1 + max(HS) >= N: continue
                        p0 = dp(te)
                        if p0 is None: continue
                        pre = 0.0; hr = (te // 3600) * 3600; nev[1] += 1
                        for h in HS:
                            p1 = dp(te + h)
                            ev = sg * math.log(p1 / p0) * 1e4
                            cs = 0.0; cn = 0
                            for _ in range(NCTL):
                                tc = hr + rnd.randrange(3600) + 1
                                q0 = dp(tc); q1 = dp(tc + h)
                                if q0 is not None and q1 is not None: cs += sg * math.log(q1 / q0) * 1e4; cn += 1
                            if cn: add(cells, f'rev|{xl}|{T}', h, dd, ev, cs / cn, pre, 0)
        return dict(coin=dirname, status=status, net=net, kind=kind, pool=best[0] if best else None, nev=nev,
                    cells={f'{k}|{h}|{dd}': v for k, hh in cells.items() for (h, dd), v in hh.items()})
    except Exception as e:
        return dict(coin=dirname, status=f'error:{type(e).__name__}:{e}')

def main():
    jobs = []
    for net in ('ethereum', 'base'):
        for dn in sorted(os.listdir(f'{ROOT}/{net}')):
            c = dn.split('-')[0]
            if c in UNI: jobs.append((dn, net))
    print('jobs', len(jobs), flush=True)
    with mp.Pool(P) as pool:
        out = []
        for i, r in enumerate(pool.imap_unordered(work, jobs), 1):
            out.append(r)
            if i % 10 == 0: print(i, len(jobs), flush=True)
    json.dump(out, open(f'{OUT}/raw.json', 'w'))
    open(f'{OUT}/DONE.json', 'w').write(json.dumps({'jobs': len(jobs), 'ok': sum(1 for r in out if r['status'] == 'ok'),
                                                    'status': dict(collections.Counter(r['status'].split('(')[0].split(':')[0] for r in out))}))

if __name__ == '__main__': main()
