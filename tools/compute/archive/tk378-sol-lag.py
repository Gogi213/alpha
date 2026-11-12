#!/usr/bin/env python3
"""TK-378 дозапуск TK-377 шаг 5: DEX-цена = последняя сделка минуты из SQD-свопов (sqd*-swaps-usd.csv, все свопы, не свечи
GeckoTerminal) vs Gate-фьюч (свечи 1 м). Метод и эпизоды — как tk377-sol.py. Запуск: tk378-sol-lag.py OUT_DIR SWAPS.csv [SWAPS.csv...]"""
import sys, os, json, math, time, random, statistics, csv, urllib.request, datetime
OUT = sys.argv[1]; os.makedirs(OUT, exist_ok=True)
D0 = int(datetime.datetime(2026, 10, 15, tzinfo=datetime.timezone.utc).timestamp()); D1 = D0 + 8 * 86400; N = 8 * 1440
SW = {}
for fn in sys.argv[2:]:
    for r in csv.DictReader(open(fn)):
        SW.setdefault(r['coin'], []).append((int(r['ts']), int(r['slot']), int(r['txidx']), float(r['price_usd'])))
COINS = sorted(SW)
def get(u):
    for k in range(6):
        try: return json.load(urllib.request.urlopen(u, timeout=20))
        except Exception as e:
            time.sleep(8 if '429' in str(e) else 2)
    return None
def dexc(c):
    a = [float('nan')] * N; n = 0
    for ts, sl, ti, p in sorted(SW[c]):
        i = (ts - D0) // 60
        if 0 <= i < N and p > 0: a[i] = p; n += 1
    return a, n
def gatec(c):
    a = [float('nan')] * N; t = D0
    while t < D1:
        e = min(t + 1000 * 60, D1)
        d = get(f'https://api.gateio.ws/api/v4/futures/usdt/candlesticks?contract={c}_USDT&interval=1m&from={t}&to={e - 1}')
        for r in d or []:
            i = (int(r['t']) - D0) // 60
            if 0 <= i < N: a[i] = float(r['c'])
        t = e; time.sleep(0.3)
    return a
def ff(a):
    l = float('nan')
    for i in range(N):
        if a[i] == a[i]: l = a[i]
        else: a[i] = l
    return a
rows = []; summ = []
for c in COINS:
    d, nd = dexc(c); g = gatec(c); ng = sum(1 for x in g if x == x)
    s = dict(coin=c, dex_minutes=nd, gate_candles=ng)
    if nd < 200 or ng < 1000: s['status'] = 'skip: мало свечей'; summ.append(s); print(c, s); continue
    gf = ff(g[:]); ok = [i for i in range(N) if d[i] == d[i] and g[i] == g[i]]
    gap = {i: math.log(g[i] / d[i]) * 1e4 for i in ok}
    med = statistics.median(gap.values()); s.update(both=len(ok), gap_median_bp=round(med, 1), gap_sd_bp=round(statistics.pstdev([v - med for v in gap.values()]), 1), status='ok')
    rnd = random.Random(377 + sum(map(ord, c)))
    for X in (50, 100, 200, 500):
        i = 1
        while i < N - 62:
            if i not in gap or abs(gap[i] - med) < X: i += 1; continue
            dev = gap[i] - med; sg = -1 if dev > 0 else 1; te = i + 1
            if gf[te] != gf[te]: i += 1; continue
            tconv = None
            for u in range(te, min(te + 60, N - 1) + 1):
                if u in gap and abs(gap[u] - med) <= X / 2: tconv = u - te; break
            hold = tconv if tconv is not None else 60; end = te + hold
            pnl = sg * math.log(gf[end] / gf[te]) * 1e4
            m = math.log(gf[i] / gf[i - 1]) * 1e4; day = i // 1440; cs = []
            tol = 1.5 if abs(m) < 3 else 0.3 * abs(m)
            if abs(m) > 0.05:
                for _ in range(300):
                    tc = day * 1440 + 1 + rnd.randrange(1440 - 2 - hold)
                    mc = math.log(gf[tc] / gf[tc - 1]) * 1e4 if gf[tc] == gf[tc] and gf[tc - 1] == gf[tc - 1] else float('nan')
                    if mc == mc and mc * m > 0 and abs(abs(mc) - abs(m)) <= tol and tc + 1 + hold < N and gf[tc + 1 + hold] == gf[tc + 1 + hold]:
                        cs.append(sg * math.log(gf[tc + 1 + hold] / gf[tc + 1]) * 1e4)
                        if len(cs) >= 10: break
            rows.append(dict(coin=c, X=X, day=15 + day, dev0=round(dev, 1), conv_min=tconv if tconv is not None else '', pnl=round(pnl, 1), ctl=round(sum(cs) / len(cs), 1) if cs else '', ctl_n=len(cs), hold=hold,
                             gate_to_dex=int(abs(math.log(gf[end] / gf[te])) * 1e4 > 0 and sg * math.log(gf[end] / gf[te]) > 0)))
            i = end + 1
    summ.append(s); print(c, s, flush=True)
json.dump(summ, open(f'{OUT}/summ.json', 'w'), ensure_ascii=False, indent=1)
if rows:
    w = csv.DictWriter(open(f'{OUT}/episodes.csv', 'w', newline=''), list(rows[0])); w.writeheader(); w.writerows(rows)
print('done', len(rows))
