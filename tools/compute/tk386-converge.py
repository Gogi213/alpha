#!/usr/bin/env python3
"""TK-386 / P-26: чистый пересчёт «схождение Gate-фьюча к DEX» по спецификации аудита TK-384 (п.1–13).
Цена DEX — оценка середины пула по паре «последняя покупка / последняя продажа» (mid=pair, оба своп ≤ 60 с до корзины), не цена исполнения; v3 sqrtPriceX96 и v2 Sync — дозапуском.
Отрыв = log(mid BBO Gate / mid DEX) - скользящая медиана за предыдущие 24 ч (без заглядывания; пересчёт раз в час; сутки 15.10 без базиса) | MODE=raw: без вычета.
Сетка (заморожена в P-26 §3): формы a=первое пересечение T, b=разворот (r=0,25 от пика), c=мейкер-лимит; T 1/2/3/5 %; выход T/2 | 0; таймаут 5м/30м/2ч/8ч = 96.
Издержки: тейкер 5 бп/нога, мейкер 2 бп/нога (стандартный тариф Gate, уточнить по API), реальный спред BBO, фандинг Gate. Контроль: 5 случайных входов той же монеты, тот же знак и длительность.
Запуск: tk386-converge.py CHAIN COINS OUTDIR   (CHAIN: solana|base|bsc; COINS: через запятую)"""
import sys, os, csv, gzip, math, json, statistics, random, datetime, urllib.request, collections
import numpy as np
CH, COINS, OUT = sys.argv[1], sys.argv[2].split(','), sys.argv[3]; os.makedirs(OUT, exist_ok=True)
MODE = os.environ.get('MODE', 'med')
B = 10; D0 = int(datetime.datetime(2026, 10, 15, tzinfo=datetime.timezone.utc).timestamp()); ND = 8; N = ND * 86400 // B
TK, MK = 5.0, 2.0
TS = (1.0, 2.0, 3.0, 5.0); HS = {'5м': 30, '30м': 180, '2ч': 720, '8ч': 2880}; RFRAC = 0.25; AGE = 6   # бин = 10 с, возраст ≤ 60 с
NAN = float('nan')
def sec(v): v = float(v); return v / 1000 if v > 1e11 else v
# --- свопы: (ts, side(+1 buy token / -1 sell), price_usd, usd)
SW = collections.defaultdict(list)
if CH == 'solana':
    for r in csv.DictReader(open('/data/tk0380/solout/swaps-usd.csv')):
        if r['coin'] not in COINS: continue
        try: px = float(r['price_usd']); usd = float(r['usd'])
        except ValueError: continue
        if usd > 0 and px > 0: SW[r['coin']].append((int(r['ts']), int(r['slot']), int(r['txidx']), 1 if r['side'] == 'buy' else -1, px, usd))
# EVM v2: середина пула из Sync (reserve0, reserve1); q0 = квота — token0; USD: USDC=1, WBNB = mid BBO Gate BNB
EVM = {'SUE': dict(pool='0xfFCa411CFbcD3194503eFfA80Fd394b55387915c', q0=False, dt=18, dq=18, quote='BNB'),
       'LAPTOP': dict(pool='0x99cf3E8bfB02c300312c53Aac5D0B082e3D5975C', q0=True, dt=18, dq=6, quote=None, v3=True)}   # LAPTOP — Slipstream (CL): sqrtPriceX96 из Swap
def evm_mid(c, bnb=None):
    e = EVM[c]; pl = e['pool'].lower(); a = np.full(N, np.nan); n = 0
    import glob
    for f in sorted(glob.glob(f'/data/tk0380/{"sqd" if e.get("v3") else "sqd-sync"}/{CH}/part-*.jsonl')):
        for line in open(f):
            if pl not in line: continue
            try: j = json.loads(line)
            except ValueError: continue
            ts = j['header']['timestamp']
            for lg in j.get('logs', []):
                if lg['address'].lower() != pl: continue
                d = lg['data'][2:]
                if e.get('v3'):
                    sq = int(d[128:192], 16)
                    if sq <= 0: continue
                    p01 = (sq / 2 ** 96) ** 2   # raw token1/token0
                    px = (1 / p01 if e['q0'] else p01) * 10 ** (e['dt'] - e['dq']) if e['q0'] else p01 * 10 ** (e['dt'] - e['dq'])
                else:
                    r0 = int(d[:64], 16); r1 = int(d[64:128], 16)
                    if r0 <= 0 or r1 <= 0: continue
                    rq, rt = (r0, r1) if e['q0'] else (r1, r0)
                    px = (rq / 10 ** e['dq']) / (rt / 10 ** e['dt'])
                i = (ts - D0) // B
                if 0 <= i < N: a[i] = px; n += 1
    o = a.copy(); l = np.nan; li = -10 ** 9
    for i in range(N):
        if a[i] == a[i]: l = a[i]; li = i
        elif i - li <= AGE: o[i] = l
    if e['quote'] == 'BNB': o = o * bnb
    print('sync', c, n, flush=True); return o
def grids(rows):
    """последняя покупка и продажа в корзине -> mid=(pb+ps)/2, если обе не старше AGE корзин."""
    pb = np.full(N, np.nan); ps = np.full(N, np.nan)
    for ts, _, _, sd, px, usd in sorted(rows):
        i = (ts - D0) // B
        if 0 <= i < N: (pb if sd > 0 else ps)[i] = px
    def ff(a):
        o = a.copy(); l = np.nan; li = -10 ** 9
        for i in range(N):
            if a[i] == a[i]: l = a[i]; li = i
            elif i - li <= AGE: o[i] = l
        return o
    b, s = ff(pb), ff(ps)
    mid = np.where((b == b) & (s == s), (b + s) / 2, np.nan)
    return mid
def loadbbo(c):
    p = f'/data/tk0380/bbo/{c}.csv.gz'
    if not os.path.exists(p): return None
    bid = np.full(N, np.nan); ask = np.full(N, np.nan)
    for line in gzip.open(p, 'rt'):
        t, b, k = line.rstrip().split(',')
        i = (int(t) - D0) // B
        if 0 <= i < N and b and k: bid[i] = float(b); ask[i] = float(k)
    for a in (bid, ask):
        l = np.nan; li = -10 ** 9
        for i in range(N):
            if a[i] == a[i]: l = a[i]; li = i
            elif i - li <= 6: a[i] = l
    return bid, ask
FP = '/data/tk0380/tk382-funding.json'
FUND = json.load(open(FP)) if os.path.exists(FP) else {}
def funding(c):
    if c not in FUND:
        try: FUND[c] = sorted((int(x['t']), float(x['r'])) for x in json.load(urllib.request.urlopen(urllib.request.Request(f'https://api.gateio.ws/api/v4/futures/usdt/funding_rate?contract={c}_USDT&limit=1000', headers={'user-agent': 'curl/8.0'}), timeout=20)))
        except Exception as e: FUND[c] = None; print('funding fail', c, e, flush=True)
    return FUND[c]
def fsum(c, ta, tb):
    f = funding(c)
    return None if f is None else sum(r for t, r in f if ta < t <= tb)
def rollbase(gap):
    """скользящая медиана предыдущих 24 ч, пересчёт раз в час; до 16.10 — NaN."""
    base = np.full(N, np.nan); W = 8640
    for k in range(W, N, 360):
        w = gap[k - W:k]; w = w[w == w]
        if len(w) >= 200: base[k:k + 360] = np.median(w)
    return base
rng = random.Random(386); eps = []; coins = []
for c in COINS:
    bb = loadbbo(c)
    if bb is None or (CH == 'solana' and c not in SW): coins.append(dict(coin=c, chain=CH, status='skip: нет BBO или свопов')); continue
    bd, ak = bb; mid = (bd + ak) / 2; mid = np.where(ak > bd, mid, np.nan)
    if CH == 'solana': dx = grids(SW[c])
    else:
        bq = None
        if EVM[c]['quote'] == 'BNB': bq = loadbbo('BNB'); bq = (bq[0] + bq[1]) / 2
        dx = evm_mid(c, bq); SW[c] = [(0, 0, 0, 0, 0, 0)]
    ok = (mid == mid) & (dx == dx)
    gap = np.where(ok, np.log(mid / dx) * 1e4, np.nan)
    base = rollbase(gap) if MODE == 'med' else np.zeros(N)
    dev = gap - base; okd = np.where(dev == dev)[0]
    sp = (ak - bd) / mid * 1e4
    s = dict(coin=c, chain=CH, swaps=len(SW[c]), usd=round(sum(r[5] for r in SW[c])), bins_valid=int(len(okd)), spread_med_bp=round(float(np.nanmedian(sp)), 2), gap_sigma_bp=round(float(np.nanstd(dev)), 1) if len(okd) else None)
    if len(okd) < 500: s['status'] = 'skip: мало корзин'; coins.append(s); continue
    s['status'] = 'ok'; coins.append(s)
    ad = np.abs(dev)
    for T in TS:
        X = T * 100
        cand = np.where(ad >= X)[0]
        for form in 'abc':
            for ex in ('half', 'zero'):
                for hn, H in HS.items():
                    i = 0; used = -1
                    for c0 in cand:
                        if c0 <= used or c0 < 6 or c0 >= N - 3: continue
                        dev0 = dev[c0]; sg = -1 if dev0 > 0 else 1
                        # форма входа -> te (бин входа), pk
                        pk = abs(dev0); te = c0 + 1
                        if form == 'b':
                            u = c0; fnd = False
                            while u < min(c0 + 2880, N - 2):
                                u += 1
                                if ad[u] != ad[u]: continue
                                if ad[u] > pk and np.sign(dev[u]) == np.sign(dev0): pk = ad[u]; continue
                                if ad[u] <= pk * (1 - RFRAC): fnd = True; break
                            if not fnd: continue
                            te = u + 1
                        lim = X / 2 if ex == 'half' else 0.0
                        if te >= N - 2 or bd[te] != bd[te] or ak[te] != ak[te]: continue
                        # мейкер-лимит: сторона схождения; заполнение — цена прошла сквозь лимит в ближайшие 6 бинов
                        if form == 'c':
                            lp = ak[te - 1] if sg < 0 else bd[te - 1]; filled = None
                            for u in range(te, min(te + 6, N - 2) + 1):
                                if (sg < 0 and bd[u] >= lp) or (sg > 0 and ak[u] <= lp): filled = u; break
                            if filled is None: continue
                            ent = lp; te2 = filled; fee_in = MK
                        else: ent = ak[te] if sg > 0 else bd[te]; te2 = te; fee_in = TK
                        tc = None; end = None
                        for u in range(te2 + 1, min(te2 + H, N - 2) + 1):
                            if dev[u] == dev[u] and -sg * dev[u] <= lim: tc = u; break
                        end = tc if tc is not None else min(te2 + H, N - 2)
                        if bd[end] != bd[end] or ak[end] != ak[end]: continue
                        ex_p = bd[end] if sg > 0 else ak[end]
                        L = end - te2; used = end
                        pnl = sg * math.log(ex_p / ent) * 1e4
                        fs = fsum(c, D0 + te2 * B, D0 + end * B); fund = None if fs is None else sg * fs * 1e4
                        gm = math.log(mid[end] / mid[te2]) * 1e4 if mid[end] == mid[end] and mid[te2] == mid[te2] else NAN
                        dm = math.log(dx[end] / dx[te2]) * 1e4 if dx[end] == dx[end] and dx[te2] == dx[te2] else NAN
                        ds = 1 if dev0 > 0 else -1
                        gt_, dt_ = -ds * gm, ds * dm   # ход Gate / ход DEX в сторону схождения, бп
                        cl = 'none' if tc is None else ('gate' if gt_ >= dt_ else 'dex')
                        cs = []
                        for _ in range(5):
                            for _t in range(20):
                                j = int(rng.choice(okd))
                                if j + 1 + L < N and bd[j + 1] == bd[j + 1] and ak[j + 1] == ak[j + 1] and bd[j + 1 + L] == bd[j + 1 + L] and ak[j + 1 + L] == ak[j + 1 + L]: break
                            else: continue
                            en = ak[j + 1] if sg > 0 else bd[j + 1]; xx = bd[j + 1 + L] if sg > 0 else ak[j + 1 + L]
                            f2 = fsum(c, D0 + (j + 1) * B, D0 + (j + 1 + L) * B)
                            cs.append(sg * math.log(xx / en) * 1e4 - 2 * TK - (0 if f2 is None else sg * f2 * 1e4))
                        eps.append(dict(chain=CH, coin=c, form=form, T=T, ex=ex, H=hn, day=15 + (te2 * B) // 86400, ts=D0 + te2 * B, sg=sg, dev0=round(float(dev0), 1), peak=round(float(pk), 1),
                                        dur_bins=L, conv=int(tc is not None), cls=cl, gate_toward=round(float(gt_), 1) if gt_ == gt_ else None, dex_toward=round(float(dt_), 1) if dt_ == dt_ else None,
                                        pnl_gross=round(pnl, 1), fund=None if fund is None else round(fund, 2), net=round(pnl - fee_in - TK - (fund or 0), 1),
                                        ctrl=round(statistics.mean(cs), 1) if cs else None))
    print(c, len(eps), flush=True)
for name, rows in (('episodes', eps), ('coins', coins)):
    if rows:
        w = csv.DictWriter(open(f'{OUT}/{name}.csv', 'w', newline=''), sorted({k for r in rows for k in r}), restval=''); w.writeheader(); w.writerows(rows)
json.dump(FUND, open(f'{OUT}/funding.json', 'w'))
open(f'{OUT}/DONE.json', 'w').write(json.dumps(dict(episodes=len(eps), coins=len(coins))))
