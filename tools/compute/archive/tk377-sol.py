#!/usr/bin/env python3
"""TK-377 шаг 5: Solana (GeckoTerminal OHLCV 1 м) vs Gate-фьюч (свечи 1 м), 15-21.10. Нижняя граница по свечам.
Запуск: tk377-sol.py OUT_DIR  (сеть: api.geckoterminal.com, api.gateio.ws; лимит GeckoTerminal ~30 запр/мин)"""
import sys, os, json, math, time, random, statistics, csv, urllib.request, datetime
OUT = sys.argv[1]; os.makedirs(OUT, exist_ok=True)
COINS = ['TQQQX', 'KIMCHI', 'CLAUDIA', 'HOOKED', 'STAMP', 'FONE']
D0 = int(datetime.datetime(2026, 10, 15, tzinfo=datetime.timezone.utc).timestamp()); D1 = D0 + 7 * 86400; N = 7 * 1440
H = {'accept': 'application/json;version=20230302'}
def get(u):
    for k in range(6):
        try: return json.load(urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=20))
        except Exception as e:
            time.sleep(8 if '429' in str(e) else 2)
    return None
def pools(c):
    d = get(f'https://api.geckoterminal.com/api/v2/search/pools?query={c}&network=solana&page=1'); time.sleep(2.5)
    out = []
    for p in (d or {}).get('data', []):
        a = p['attributes']
        if a['name'].split('/')[0].strip().upper() != c: continue
        out.append((p['id'].split('_')[1], a['pool_created_at'], float(a['reserve_in_usd']), float(a['volume_usd']['h24'])))
    return out
def dexc(pool):
    a = [float('nan')] * N; n = 0; b = D1
    while b > D0:
        d = get(f'https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool}/ohlcv/minute?aggregate=1&before_timestamp={b}&limit=1000&currency=usd'); time.sleep(2.5)
        l = (d or {}).get('data', {}).get('attributes', {}).get('ohlcv_list', []) if d else []
        if not l: break
        for t, o, h, lo, c, v in l:
            i = (t - D0) // 60
            if 0 <= i < N: a[i] = c; n += 1
        b = min(x[0] for x in l)
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
    ps = [p for p in pools(c) if p[1] <= '2026-10-15T00:00:00Z']
    if not ps: summ.append(dict(coin=c, status='нет пула старше 15.10')); print(c, 'нет пула'); continue
    ps.sort(key=lambda p: -p[3]); pool = ps[0][0]
    d, nd = dexc(pool); g = gatec(c); ng = sum(1 for x in g if x == x)
    s = dict(coin=c, pool=pool, created=ps[0][1], dex_candles=nd, gate_candles=ng)
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
