#!/usr/bin/env python3
"""TK-382: крупные отрывы фьюча Gate от DEX (|gap-мед| >= T = 2/3/5 %) с долгим удержанием (таймаут H = 30 мин / 2 ч / 8 ч), по сетям TK-380.
Цена фьюча = середина BBO (стакан TK-379, /data/tk0380/bbo), цена DEX = своп-ряд TK-380 (очистка как в tk380-*-lag3), сетка 10 с, ffill DEX до 30 мин (флаг stale: последний своп > 5 мин до входа).
Эпизод: вход на след. корзине тейкером по BBO (фьюч выше DEX -> шорт по бид, ниже -> лонг по аск), выход при |gap-мед| <= T/2 или таймаут, выход тейкером. net = pnl - RT 15 бп - фандинг Gate за время удержания.
MAE: макс. неблагоприятный ход по исполнимой стороне; doubled: |gap-мед| достиг 2*T0 (в бине входа) до выхода. Контроль: 5 случайных входов той же монеты, тот же знак и длительность.
Запуск: tk382-biggap.py CHAIN OUTDIR  (CHAIN: bsc|ethereum|base|arbitrum|solana)"""
import sys, os, csv, gzip, math, json, statistics, random, datetime, urllib.request, collections
CH = sys.argv[1]; OUT = sys.argv[2]; os.makedirs(OUT, exist_ok=True)
R = '/srv/alpha-sda/tk372-gate/raw'; B = 10; RT = 15.0
D0 = int(datetime.datetime(2026, 10, 15, tzinfo=datetime.timezone.utc).timestamp()); ND = 8; N = ND * 86400 // B
TS = (2.0, 3.0, 5.0); HS = {'30м': 180, '2ч': 720, '8ч': 2880}
CFG = {
    'bsc': dict(rpc='https://bsc-dataseed.bnbchain.org', native=('WBNB', 'BNB'), qa={'USDT': ('0x55d398326f99059ff775485246999027b3197955', 18), 'USDC': ('0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d', 18), 'USD1': ('0x8d0d000ee44948fc98c9b98a4fa4921476f08b0d', 18), 'WBNB': ('0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c', 18)}),
    'ethereum': dict(rpc='https://ethereum-rpc.publicnode.com', native=('WETH', 'ETH'), qa={'USDT': ('0xdac17f958d2ee523a2206206994597c13d831ec7', 6), 'USDC': ('0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48', 6), 'WETH': ('0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2', 18), 'ETH': ('0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2', 18)}),
    'base': dict(rpc='https://mainnet.base.org', native=('WETH', 'ETH'), qa={'USDC': ('0x833589fcd6edb6e08f4c7c32d4f71b54bda02913', 6), 'WETH': ('0x4200000000000000000000000000000000000006', 18), 'ETH': ('0x4200000000000000000000000000000000000006', 18)}),
    'arbitrum': dict(rpc='https://arb1.arbitrum.io/rpc', native=('WETH', 'ETH'), qa={'USDC': ('0xaf88d065e77c8cc2239327c5edb3a432268e5831', 6), 'WETH': ('0x82af49447d8a07e3bd95bd0d56f35241523fbab1', 18)}),
}
def sec(v): v = float(v); return v / 1000 if v > 1e11 else v
SW = collections.defaultdict(list); drop = collections.Counter()
if CH == 'solana':
    for r in csv.DictReader(open('/data/tk0380/solout/swaps-usd.csv')):
        try: px = float(r['price_usd']); usd = float(r['usd'])
        except ValueError: drop['bad'] += 1; continue
        if usd <= 0 or px <= 0: continue
        SW[r['coin']].append((int(r['ts']), int(r['slot']), int(r['txidx']), 0, px, usd, r['quote']))
else:
    C = CFG[CH]; QA = {k: v[0] for k, v in C['qa'].items()}; QD = {k: v[1] for k, v in C['qa'].items()}; NAT = C['native']
    pools = {}
    for r in csv.DictReader(open('/data/tk0380/pools-sqd.csv')):
        if r['chain'] == CH and r['quote'] in QA: pools[r['pool'].lower()] = r
    def rpc(to, data):
        q = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'eth_call', 'params': [{'to': to, 'data': data}, 'latest']}).encode()
        for _ in range(4):
            try: return json.load(urllib.request.urlopen(urllib.request.Request(C['rpc'], q, {'content-type': 'application/json', 'user-agent': 'curl/8.0'}), timeout=15))['result']
            except Exception: pass
    dec = {}
    for r in pools.values():
        t = r['token'].lower()
        if t not in dec:
            v = rpc(t, '0x313ce567'); dec[t] = int(v, 16) if v and len(v) > 2 else None
    nat = {}; nd = f'{R}/fut/{NAT[1]}_USDT'
    for fn in sorted(os.listdir(nd)):
        for r in csv.DictReader(gzip.open(f'{nd}/{fn}', 'rt')): nat[int(sec(r['create_time_ms'] or r['create_time']) // 60)] = float(r['price'])
    def natpx(ts):
        m = ts // 60
        for k in (0, -1, 1, -2, 2):
            if m + k in nat: return nat[m + k]
    for r in csv.DictReader(gzip.open(f'/data/tk0380/sqd/{CH}-swaps/swaps.csv.gz', 'rt')):
        p = pools.get(r['pool'])
        if not p: continue
        td = dec.get(p['token'].lower())
        if td is None: drop['no_dec'] += 1; continue
        a0, a1 = int(r['amount0']), int(r['amount1']); t0 = p['token'].lower() < QA[p['quote']]
        at, aq = (a0, a1) if t0 else (a1, a0)
        if at == 0 or aq == 0: continue
        qq = abs(aq) / 10 ** QD[p['quote']]; qt = abs(at) / 10 ** td; ts = int(r['ts'])
        if p['quote'] in NAT:
            b = natpx(ts)
            if b is None: drop['no_nat'] += 1; continue
            usd = qq * b
        else: usd = qq
        if usd <= 0 or qt <= 0: continue
        SW[p['coin']].append((ts, int(r['block']), int(r['txidx']), int(r['logidx']), usd / qt, usd, p['quote']))
def grid(pts):
    a = [float('nan')] * N
    for t, p in pts:
        i = int(t - D0) // B
        if 0 <= i < N: a[i] = p
    return a
def ffill(a, lim):
    o = a[:]; l = None; li = -10 ** 9
    for i in range(N):
        if a[i] == a[i]: l = a[i]; li = i
        elif l is not None and i - li <= lim: o[i] = l
    return o
def lastidx(a):  # индекс последней реальной точки до i
    o = [-10 ** 9] * N; l = -10 ** 9
    for i in range(N):
        if a[i] == a[i]: l = i
        o[i] = l
    return o
def clean(rows, usd_min=5.0, band=0.1, win=30):
    rows = sorted(rows); ps = [r[4] for r in rows]; out = []
    for k, r in enumerate(rows):
        if r[5] < usd_min: continue
        w = sorted(ps[max(0, k - win):k + win + 1]); m = w[len(w) // 2]
        if abs(r[4] / m - 1) > band: continue
        out.append(r)
    return out
def loadbbo(c):
    p = f'/data/tk0380/bbo/{c}.csv.gz'
    if not os.path.exists(p): return None
    bid = [float('nan')] * N; ask = [float('nan')] * N
    for line in gzip.open(p, 'rt'):
        t, b, k = line.rstrip().split(',')
        i = (int(t) - D0) // B
        if 0 <= i < N and b and k: bid[i] = float(b); ask[i] = float(k)
    return ffill(bid, 6), ffill(ask, 6)
FP = '/data/tk0380/tk382-funding.json'
FUND = json.load(open(FP)) if os.path.exists(FP) else {}
def funding(c):
    if c not in FUND:
        try: FUND[c] = sorted((int(x['t']), float(x['r'])) for x in json.load(urllib.request.urlopen(urllib.request.Request(f'https://api.gateio.ws/api/v4/futures/usdt/funding_rate?contract={c}_USDT&limit=1000', headers={'user-agent': 'curl/8.0'}), timeout=20)))
        except Exception as e: FUND[c] = None; print('funding fail', c, e, flush=True)
        json.dump(FUND, open(f'{OUT}/funding-{CH}.json', 'w'))
    return FUND[c]
def fsum(c, ta, tb):  # сумма ставок фандинга, расчёты в (ta, tb]
    f = funding(c)
    if f is None: return None
    return sum(r for t, r in f if ta < t <= tb)
rng = random.Random(382); eps = []; coins = []
for c in sorted(SW):
    bb = loadbbo(c)
    if bb is None: coins.append(dict(coin=c, status='skip: нет BBO')); continue
    bd, ak = bb
    q = collections.Counter()
    for r in SW[c]: q[r[6]] += r[5]
    qm = q.most_common(1)[0][0]
    sw = clean([r for r in SW[c] if r[6] == qm])
    raw = grid([(r[0], r[4]) for r in sw]); dg = ffill(raw, 180); li = lastidx(raw)
    mid = [(bd[i] + ak[i]) / 2 if bd[i] == bd[i] and ak[i] == ak[i] and ak[i] > bd[i] else float('nan') for i in range(N)]
    ok = [i for i in range(N) if dg[i] == dg[i] and mid[i] == mid[i]]
    sp = [(ak[i] - bd[i]) / mid[i] * 1e4 for i in range(N) if mid[i] == mid[i]]
    s = dict(coin=c, quote=qm, swaps_clean=len(sw), usd=round(sum(r[5] for r in sw)), bins_both=len(ok), spread_med_bp=round(statistics.median(sp), 2) if sp else None)
    if len(ok) < 500: s['status'] = 'skip: мало корзин'; coins.append(s); continue
    gap = {i: math.log(mid[i] / dg[i]) * 1e4 for i in ok}
    med = statistics.median(gap.values()); s.update(gap_median_bp=round(med, 1), sigma_bp=round(statistics.pstdev([v - med for v in gap.values()]), 1), status='ok'); coins.append(s)
    for T in TS:
        X = T * 100
        for hn, H in HS.items():
            i = 6
            while i < N - 2:
                if i not in gap or abs(gap[i] - med) < X: i += 1; continue
                dev0 = gap[i] - med; sg = -1 if dev0 > 0 else 1; te = i + 1; tc = None; dbl = 0; pk = abs(dev0)
                for u in range(te, min(te + H, N - 1) + 1):
                    if u in gap:
                        a = abs(gap[u] - med)
                        if a > pk: pk = a
                        if a >= 2 * abs(dev0): dbl = 1
                        if a <= X / 2: tc = u - te; break
                end = te + (tc if tc is not None else min(H, N - 1 - te))
                if mid[te] != mid[te] or bd[te] != bd[te] or ak[te] != ak[te] or bd[end] != bd[end] or ak[end] != ak[end]: i += 1; continue
                ent = ak[te] if sg > 0 else bd[te]; ex = bd[end] if sg > 0 else ak[end]
                mae = 0.0
                for u in range(te, end + 1):
                    if bd[u] == bd[u] and ak[u] == ak[u]:
                        adv = math.log(ent / bd[u]) if sg > 0 else math.log(ak[u] / ent)
                        if adv * 1e4 > mae: mae = adv * 1e4
                ta = D0 + te * B; tb = D0 + end * B
                fs = fsum(c, ta, tb)
                pnl = sg * math.log(ex / ent) * 1e4
                fund = None if fs is None else sg * fs * 1e4
                L = end - te; cs = []
                for _ in range(5):
                    for _t in range(20):
                        j = rng.choice(ok)
                        if j + 1 + L < N and bd[j + 1] == bd[j + 1] and ak[j + 1] == ak[j + 1] and bd[j + 1 + L] == bd[j + 1 + L] and ak[j + 1 + L] == ak[j + 1 + L]: break
                    else: continue
                    en = ak[j + 1] if sg > 0 else bd[j + 1]; xx = bd[j + 1 + L] if sg > 0 else ak[j + 1 + L]
                    f2 = fsum(c, D0 + (j + 1) * B, D0 + (j + 1 + L) * B)
                    cs.append(sg * math.log(xx / en) * 1e4 - RT - (0 if f2 is None else sg * f2 * 1e4))
                eps.append(dict(chain=CH, coin=c, T=T, H=hn, day=15 + i * B // 86400, ts=ta, sg=sg, dev0=round(dev0, 1), peak=round(pk, 1), dur_bins=L, conv=int(tc is not None), doubled=dbl,
                                stale=int(i - li[i] > 30), pnl_x=round(pnl, 1), mae=round(mae, 1), fund=None if fund is None else round(fund, 2),
                                net=round(pnl - RT - (fund or 0), 1), ctrl=round(statistics.mean(cs), 1) if cs else None))
                i = end + 1
    print(c, len(eps), flush=True)
for name, rows in (('episodes', eps), ('coins', coins)):
    if rows:
        w = csv.DictWriter(open(f'{OUT}/{name}.csv', 'w', newline=''), sorted({k for r in rows for k in r}), restval=''); w.writeheader(); w.writerows(rows)
open(f'{OUT}/DONE.json', 'w').write(json.dumps(dict(episodes=len(eps), coins=len(SW))))
