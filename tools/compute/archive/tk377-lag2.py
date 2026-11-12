#!/usr/bin/env python3
"""TK-377 шаг 3: отрывы Gate-фьючерса от DEX (ETH/Base) по ВСЕМ монетам Gate-фьюч с пулом, 15-21.10. Запуск на calc: tk377-lag2.py OUT_DIR COINS_CSV [workers]
Метод как tk377-lag.py (gap=ln(Gate/DEX)*1e4, dev=gap-медиана, эпизод |dev|>=X, вход против отрыва t+1 с, сведение |dev|<=X/2, таймаут 3600 с), плюс:
 - возраст последней сделки Gate (gate_age) и последнего свопа DEX (dex_age) на входе; чистые эпизоды: оба <= 60 с;
 - контроль по ходу Gate (решение Судьи 5): моменты того же дня с тем же знаком и |ходом Gate за 60 с| в [0,7;1,3]*m (m<3 бп: +-1,5 бп), тот же знак входа, тот же срок;
 - класс (с тяжеловесами / без) из COINS_CSV (tk377-universe-*-coins.csv, Gate)."""
import sys, os, csv, gzip, json, math, statistics, array, collections, random
from multiprocessing import Pool
OUT = sys.argv[1]; CL = sys.argv[2]; NW = int(sys.argv[3]) if len(sys.argv) > 3 else 8
os.makedirs(OUT, exist_ok=True)
ROOT = '/data/tk0374/usd'; GR = '/srv/alpha-sda/tk372-gate/raw/fut'; D0 = 1792022400; DAYS = range(15, 22); N = 7 * 86400
XS = (50, 100, 200, 500); TOS = (600, 3600)
def ffill(a):
    age = array.array('i', [-1]) * N; last = float('nan'); la = -10**9
    for i in range(N):
        if a[i] == a[i]: last = a[i]; la = i
        else: a[i] = last
        age[i] = i - la if la > -10**9 else -1
    return age
def gate(coin):
    a = array.array('d', [float('nan')]) * N; n = 0
    for day in DAYS:
        f = f'{GR}/{coin}_USDT/2026-10-{day:02d}.csv.gz'
        if not os.path.exists(f): continue
        with gzip.open(f, 'rt') as fh:
            h = fh.readline().rstrip('\n').split(','); ti, pi = h.index('create_time_ms'), h.index('price')
            for l in fh:
                s = l.split(',')
                try: v = float(s[ti]); i = int(v / 1000 if v > 1e11 else v) - D0
                except ValueError: continue
                if 0 <= i < N: a[i] = float(s[pi]); n += 1
    return a, n
def dexdirs():
    m = collections.defaultdict(list)
    for net in ('ethereum', 'base'):
        for d in os.listdir(f'{ROOT}/{net}'): m[d.split('-')[0]].append((net, d))
    return m
def dex(coin, dirs):
    best = None
    for net, d in dirs:
        pools = collections.defaultdict(list)
        for day in DAYS:
            p = f'{ROOT}/{net}/{d}/2026-10-{day:02d}.csv'
            if os.path.exists(p):
                for r in csv.DictReader(open(p)):
                    if r['price_usd']: pools[r['pool']].append((int(r['ts']), int(r['logidx']), float(r['price_usd'])))
        for pool, rows in pools.items():
            if best is None or len(rows) > len(best[2]): best = (net, pool, rows)
    if best is None: return None
    net, pool, rows = best; rows.sort()
    a = array.array('d', [float('nan')]) * N
    for ts, _, px in rows:
        i = ts - D0
        if 0 <= i < N: a[i] = px
    return net, pool, len(rows), a
def work(arg):
    coin, dirs, cls = arg; rnd = random.Random(377 + sum(map(ord, coin)))
    g, ng = gate(coin); dd = dex(coin, dirs)
    if dd is None or ng < 500: return coin, [], dict(coin=coin, cls=cls, status='skip: нет DEX/сделок Gate', gate_trades=ng)
    net, pool, nsw, d = dd; gage = ffill(g); dage = ffill(d)
    ok = [i for i in range(N) if g[i] == g[i] and d[i] == d[i]]
    if nsw < 200 or len(ok) < 86400: return coin, [], dict(coin=coin, cls=cls, net=net, swaps=nsw, gate_trades=ng, status='skip: мало свопов/перекрытия', sec_both=len(ok))
    r = statistics.median(g[i] / d[i] for i in ok[::600])
    if not (0.8 < r < 1.25): return coin, [], dict(coin=coin, cls=cls, net=net, swaps=nsw, gate_trades=ng, status=f'skip: цена не совпала {r:.2f}', sec_both=len(ok))
    gap = array.array('d', [float('nan')]) * N
    for i in ok: gap[i] = math.log(g[i] / d[i]) * 1e4
    med = statistics.median(gap[i] for i in ok[::60])
    summ = dict(coin=coin, cls=cls, net=net, pool=pool, swaps=nsw, gate_trades=ng, sec_both=len(ok), gap_median_bp=round(med, 1), gap_sd_bp=round(statistics.pstdev([gap[i] - med for i in ok[::60]]), 1), status='ok')
    res = []
    for X in XS:
        t = 60
        while t < N - 3700:
            dev = gap[t] - med if gap[t] == gap[t] else float('nan')
            if not (dev == dev and abs(dev) >= X): t += 1; continue
            s = -1 if dev > 0 else 1; te = t + 1; day = t // 86400
            if g[te] != g[te] or d[te] != d[te]: t += 1; continue
            tconv = None; mae = 0.0; dev_e = abs(gap[te] - med)
            for u in range(te, min(te + 3600, N - 1) + 1):
                dv = gap[u] - med
                if dv != dv: continue
                if abs(dv) - dev_e > mae: mae = abs(dv) - dev_e
                if abs(dv) <= X / 2: tconv = u - te; break
            hold = tconv if tconv is not None else 3600; end = te + hold
            dg = math.log(g[end] / g[te]) * 1e4; dxm = math.log(d[end] / d[te]) * 1e4
            m = math.log(g[t] / g[t - 60]) * 1e4
            row = dict(coin=coin, cls=cls, net=net, X=X, day=15 + day, t_entry=te, dev0=round(dev, 1), gate_age=gage[t], dex_age=dage[t], conv_s=tconv if tconv is not None else '', gate_part=round(s * dg, 1), dex_part=round(-s * dxm, 1), mae=round(mae, 1), m60=round(m, 1), hold_s=hold)
            tol = 1.5 if abs(m) < 3 else 0.3 * abs(m); cs = []
            if abs(m) > 0.05:
                lo = day * 86400 + 60
                for _ in range(300):
                    tc = lo + rnd.randrange(86400 - 60 - hold - 1)
                    mc = math.log(g[tc] / g[tc - 60]) * 1e4
                    if mc * m > 0 and abs(abs(mc) - abs(m)) <= tol and tc + hold < N and gage[tc] <= 60:
                        cs.append(s * math.log(g[tc + 1 + hold] / g[tc + 1]) * 1e4)
                        if len(cs) >= 10: break
            row['ctl_gm'] = round(sum(cs) / len(cs), 1) if cs else ''; row['ctl_n'] = len(cs)
            for to in TOS:
                e = te + min(hold, to); row[f'pnl{to}'] = round(s * math.log(g[e] / g[te]) * 1e4, 1)
            res.append(row); t = te + hold + 1
    return coin, res, summ
if __name__ == '__main__':
    cls = {}
    for r in csv.DictReader(open(CL)):
        if r['exchange'] == 'Gate': cls[r['base']] = r['cls']
    dm = dexdirs(); gc = {d.rsplit('_USDT', 1)[0] for d in os.listdir(GR)}
    jobs = [(c, dm[c], cls.get(c, '?')) for c in sorted(gc & set(dm))]
    print('coins', len(jobs), flush=True)
    rows = []; summ = []
    with Pool(NW) as p:
        for k, (c, rs, sm) in enumerate(p.imap_unordered(work, jobs)):
            rows += rs; summ.append(sm); print(k, c, sm.get('status'), len(rs), flush=True)
    json.dump(summ, open(f'{OUT}/summ.json', 'w'), ensure_ascii=False)
    if rows:
        cols = list(rows[0]); w = csv.DictWriter(open(f'{OUT}/episodes.csv', 'w', newline=''), cols); w.writeheader(); w.writerows(rows)
    print('done', len(rows), flush=True)
