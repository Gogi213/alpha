#!/usr/bin/env python3
"""TK-380 v3 (BBO фьюча из стакана TK-379, исполнимые цены тейкер вход/выход; мейкер-вход со сквозной сделкой): отрывы Gate (фьюч и спот, сделки) vs DEX BSC (SQD-свопы 15-22.10), по методике TK-378 v3.
Цена DEX: quote-нога USDT/USDC/USD1 = $, WBNB x BNB_USDT фьюч Gate (последняя цена минуты). Один quote на монету (макс. $), выбросы +-10% от медианы +-30 соседей.
Сетка 10 с, эпизоды |gap-med|>=X, закрытие при <=X/2 или 5 мин, вход на следующей корзине; net = pnl - RT(тейкер) - S(Gate-нога, пары сделок <=1 с).
Запуск: tk380-bsc-lag.py OUTDIR  (calc: /data/tk0380/sqd/bsc-swaps, pools-sqd.csv, Gate /srv/alpha-sda/tk372-gate/raw)"""
import bisect, sys, os, csv, gzip, math, json, statistics, random, datetime, urllib.request, collections
OUT = sys.argv[1]; os.makedirs(OUT, exist_ok=True)
R = '/srv/alpha-sda/tk372-gate/raw'; B = 10; HM = 30
D0 = int(datetime.datetime(2026, 10, 15, tzinfo=datetime.timezone.utc).timestamp()); ND = 8; N = ND * 86400 // B
QA = {'USDT': '0x55d398326f99059ff775485246999027b3197955', 'USDC': '0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d', 'USD1': '0x8d0d000ee44948fc98c9b98a4fa4921476f08b0d', 'WBNB': '0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c'}
pools = {}
for r in csv.DictReader(open('/data/tk0380/pools-sqd.csv')):
    if r['chain'] == 'bsc' and r['quote'] in QA: pools[r['pool'].lower()] = r
def rpc(to, data):
    q = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'eth_call', 'params': [{'to': to, 'data': data}, 'latest']}).encode()
    for _ in range(4):
        try: return json.load(urllib.request.urlopen(urllib.request.Request('https://bsc-dataseed.bnbchain.org', q, {'content-type': 'application/json'}), timeout=15))['result']
        except Exception: pass
dec = {}
for r in pools.values():
    t = r['token'].lower()
    if t not in dec:
        v = rpc(t, '0x313ce567'); dec[t] = int(v, 16) if v and len(v) > 2 else None
# BNB цена: Gate фьюч BNB_USDT, последняя цена в минуте
def sec(v): v = float(v); return v / 1000 if v > 1e11 else v  # fut: create_time_ms уже в секундах, spot: в мс
bnb = {}
for fn in sorted(os.listdir(f'{R}/fut/BNB_USDT')):
    for r in csv.DictReader(gzip.open(f'{R}/fut/BNB_USDT/{fn}', 'rt')): bnb[int(sec(r['create_time_ms'] or r['create_time']) // 60)] = float(r['price'])
def bnbpx(ts):
    m = ts // 60
    for k in (0, -1, 1, -2, 2):
        if m + k in bnb: return bnb[m + k]
SW = collections.defaultdict(list); drop = collections.Counter()
for r in csv.DictReader(gzip.open('/data/tk0380/sqd/bsc-swaps/swaps.csv.gz', 'rt')):
    p = pools.get(r['pool'])
    if not p: continue
    td = dec.get(p['token'].lower())
    if td is None: drop['no_dec'] += 1; continue
    a0, a1 = int(r['amount0']), int(r['amount1']); t0 = p['token'].lower() < QA[p['quote']]
    at, aq = (a0, a1) if t0 else (a1, a0)
    if at == 0 or aq == 0: continue
    qq = abs(aq) / 1e18; qt = abs(at) / 10 ** td
    ts = int(r['ts'])
    if p['quote'] == 'WBNB':
        b = bnbpx(ts)
        if b is None: drop['no_bnb'] += 1; continue
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
def clean(rows, usd_min=5.0, band=0.1, win=30):
    rows = sorted(rows); ps = [r[4] for r in rows]; out = []
    for k, r in enumerate(rows):
        if r[5] < usd_min: continue
        w = sorted(ps[max(0, k - win):k + win + 1]); m = w[len(w) // 2]
        if abs(r[4] / m - 1) > band: continue
        out.append(r)
    return out
def load(kind, c):
    d = f'{R}/{kind}/{c}_USDT'
    if not os.path.isdir(d): return None
    tr = []
    for fn in sorted(os.listdir(d)):
        for r in csv.DictReader(gzip.open(f'{d}/{fn}', 'rt')):
            t = sec(r['create_time_ms'] or r['create_time'])
            sg = (1 if float(r['size']) > 0 else -1) if kind == 'fut' else (1 if r['side'] == 'buy' else -1)
            tr.append((t, sg, float(r['price'])))
    tr.sort(key=lambda x: x[0]); return tr
def spread(tr):  # {день: (mean, med, n)}
    by = collections.defaultdict(list)
    for a, b in zip(tr, tr[1:]):
        if b[0] - a[0] > 1.0 or a[1] * b[1] >= 0: continue
        pb, ps = (a[2], b[2]) if a[1] > 0 else (b[2], a[2])
        by[int(a[0] - D0) // 86400].append((pb - ps) / ((pb + ps) / 2) * 1e4)
    return {d: (statistics.mean(v), statistics.median(v), len(v)) for d, v in by.items() if len(v) >= 20}
def loadbbo(c):
    p = f'/data/tk0380/bbo/{c}.csv.gz'
    if not os.path.exists(p): return None
    bid = [float('nan')] * N; ask = [float('nan')] * N
    for line in gzip.open(p, 'rt'):
        t, b, k = line.rstrip().split(',')
        i = (int(t) - D0) // B
        if 0 <= i < N and b and k: bid[i] = float(b); ask[i] = float(k)  # последнее состояние корзины
    return ffill(bid, 6), ffill(ask, 6)
eps = []; summ = []; spr = []
for c in sorted(SW):
    q = collections.Counter()
    for r in SW[c]: q[r[6]] += r[5]
    qm = q.most_common(1)[0][0]
    sw = clean([r for r in SW[c] if r[6] == qm])
    dg = ffill(grid([(r[0], r[4]) for r in sw]), 6)
    for kind, RT in (('fut', 15.0), ('spot', 40.0)):
        tr = load(kind, c)
        if not tr: continue
        gf = ffill(grid([(t, p) for t, s, p in tr]), 6)
        ok = [i for i in range(N) if dg[i] == dg[i] and gf[i] == gf[i]]
        s = dict(coin=c, kind=kind, quote=qm, swaps=len(SW[c]), swaps_clean=len(sw), usd=round(sum(r[5] for r in sw)), gate_trades=len(tr), bins_both=len(ok))
        if len(ok) < 500: s['status'] = 'skip: мало корзин'; summ.append(s); continue
        sp = spread(tr)
        bb = loadbbo(c) if kind == 'fut' else None
        if kind == 'fut' and bb is None: s['status'] = 'skip: нет BBO'; continue
        if bb:
            bd, ak = bb; v = [(ak[i] - bd[i]) / ((ak[i] + bd[i]) / 2) * 1e4 for i in range(N) if bd[i] == bd[i] and ak[i] == ak[i] and ak[i] > bd[i]]
            s['bbo_spread_med_bp'] = round(statistics.median(v), 2) if v else None
            tt = [x[0] for x in tr]
        for d, v in sp.items(): spr.append(dict(coin=c, kind=kind, day=15 + d, mean_bp=round(v[0], 1), med_bp=round(v[1], 1), n_pairs=v[2]))
        gap = {i: math.log(gf[i] / dg[i]) * 1e4 for i in ok}
        med = statistics.median(gap.values()); s.update(gap_median_bp=round(med, 1), sigma_bp=round(statistics.pstdev([v - med for v in gap.values()]), 1), status='ok'); summ.append(s)
        for X in (50, 100, 200):
            i = 6
            while i < N - HM - 2:
                if i not in gap or abs(gap[i] - med) < X: i += 1; continue
                dev = gap[i] - med; sg = -1 if dev > 0 else 1; te = i + 1; tc = None
                for u in range(te, te + HM + 1):
                    if u in gap and abs(gap[u] - med) <= X / 2: tc = u - te; break
                end = te + (tc if tc is not None else HM)
                if gf[te] != gf[te] or gf[end] != gf[end]: i += 1; continue
                e = dict(coin=c, kind=kind, X=X, day=15 + i * B // 86400, pnl=round(sg * math.log(gf[end] / gf[te]) * 1e4, 1), RT=RT)
                if bb and bd[te] == bd[te] and bd[end] == bd[end]:
                    ent = ak[te] if sg > 0 else bd[te]; ex = bd[end] if sg > 0 else ak[end]
                    e['pnl_x'] = round(sg * math.log(ex / ent) * 1e4, 1)  # тейкер: вход по аску (лонг) / биду (шорт), выход по противоположной стороне
                    # мейкер-вход: лимит на стороне касания (лонг = бид); исполнен, если до end была сделка СКВОЗЬ цену
                    lp = bd[te] if sg > 0 else ak[te]; t0 = D0 + te * B; t1 = D0 + (end + 1) * B
                    a_ = bisect.bisect_left(tt, t0); b_ = bisect.bisect_left(tt, t1)
                    thr = [x for x in tr[a_:b_] if (x[2] < lp if sg > 0 else x[2] > lp)]
                    e['filled'] = 1 if thr else 0
                    if thr: e['pnl_m'] = round(sg * math.log(ex / lp) * 1e4, 1)
                eps.append(e)
                i = end + 1
    print(c, len(eps), flush=True)
json.dump(dict(summ=summ, drop=dict(drop), no_dec=[r['token'] for r in pools.values() if dec.get(r['token'].lower()) is None]), open(f'{OUT}/summ.json', 'w'), ensure_ascii=False, indent=1)
for name, rows in (('episodes', eps), ('spread', spr)):
    if rows:
        w = csv.DictWriter(open(f'{OUT}/{name}.csv', 'w', newline=''), sorted({k for r in rows for k in r}), restval=''); w.writeheader(); w.writerows(rows)
open(f'{OUT}/DONE.json', 'w').write(json.dumps(dict(episodes=len(eps), coins=len(SW))))
