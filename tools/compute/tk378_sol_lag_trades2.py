#!/usr/bin/env python3
"""TK-378 v2 (возврат Судьи 01:06): один квот-токен основного пула, TQQQX исключён, σ до/после, fee DEX из свопов.
TK-378 дозапуск TK-377: отрыв Gate-фьюч (сделки, мс) vs DEX Solana (SQD-свопы, USD), сетка 10 с, 15–21.10 UTC.
Запуск: tk378_sol_lag_trades.py OUT_DIR SWAPS.csv  (Gate: /srv/alpha-sda/tk372-gate/raw/fut/<COIN>_USDT/<date>.csv.gz)"""
import sys, os, csv, gzip, math, json, statistics, random, datetime
OUT = sys.argv[1]; os.makedirs(OUT, exist_ok=True)
G = '/srv/alpha-sda/tk372-gate/raw/fut'; B = 10
D0 = int(datetime.datetime(2026, 10, 15, tzinfo=datetime.timezone.utc).timestamp()); N = 8 * 86400 // B
SW = {}
for r in csv.DictReader(open(sys.argv[2])):
    try: p = float(r['price_usd']); u = float(r['usd'])
    except ValueError: continue
    SW.setdefault(r['coin'], []).append((int(r['ts']), int(r['slot']), int(r['txidx']), p, u, r['quote'], int(r['fee'] or 0)))
SW.pop('TQQQX', None)  # маппинг mint<->Gate не подтверждён: DEX 142,2 $ vs Gate 72,4 $ (x0,509)
def grid(pts):  # pts: [(t_s, price)] по времени -> последняя цена в корзине
    a = [float('nan')] * N
    for t, p in pts:
        i = (t - D0) // B
        if 0 <= i < N: a[i] = p
    return a
def ffill(a, lim):
    o = a[:]; l = None; li = -10**9
    for i in range(N):
        if a[i] == a[i]: l = a[i]; li = i
        elif l is not None and i - li <= lim: o[i] = l
    return o
MAINQ = {'CLAUDIA': 'WSOL', 'FONE': 'WSOL', 'HOOKED': 'WSOL', 'KIMCHI': 'WSOL'}  # пул-реестр TK-380: quote SOL; у SI (NVDAx) и STAMP (ZEC) главного пула в слое свопов нет -> квот с макс. USD
def pickq(rows, c):
    if c in MAINQ: return MAINQ[c]
    v = {}
    for r in rows: v[r[5]] = v.get(r[5], 0) + r[4]
    return max(v, key=v.get)
def clean(rows, usd_min=5.0, band=0.3, win=30):  # свопы на свой слот-порядок; отсев пыли и выбросов цены (медиана ±win соседей)
    rows = sorted(rows); out = []; n = len(rows); dropped = 0
    ps = [r[3] for r in rows]
    for k, r in enumerate(rows):
        if r[4] < usd_min or r[3] <= 0: dropped += 1; continue
        w = sorted(ps[max(0, k - win):k + win + 1]); m = w[len(w) // 2]
        if abs(r[3] / m - 1) > band: dropped += 1; continue
        out.append(r)
    return out, dropped, n
rows_ep = []; summ = []
for c in sorted(SW):
    d = os.path.join(G, f'{c}_USDT')
    if not os.path.isdir(d): summ.append(dict(coin=c, status='нет Gate-фьюч')); continue
    gp = []
    for fn in sorted(os.listdir(d)):
        for r in csv.DictReader(gzip.open(os.path.join(d, fn), 'rt')):
            gp.append((int(float(r['create_time_ms'])) if r.get('create_time_ms') else int(float(r['create_time'])), float(r['price'])))
    gg = grid(sorted(gp)); gf = ffill(gg, 6)
    def sigma(sw_):
        d_ = ffill(grid([(r[0], r[3]) for r in sw_]), 6); o_ = [i for i in range(N) if d_[i] == d_[i] and gf[i] == gf[i]]
        if len(o_) < 500: return None, d_, o_
        g_ = [math.log(gf[i] / d_[i]) * 1e4 for i in o_]; m_ = statistics.median(g_)
        return round(statistics.pstdev([v - m_ for v in g_]), 1), d_, o_
    sw0, _, _ = clean(SW[c]); sig_before, _, _ = sigma(sw0)  # v1: любой своп с mint
    q = pickq(SW[c], c); rq = [r for r in SW[c] if r[5] == q]
    sw, dr, n0 = clean(rq, band=0.1)
    fees = sorted(r[6] for r in rq)
    dg = ffill(grid([(r[0], r[3]) for r in sw]), 6)
    ok = [i for i in range(N) if dg[i] == dg[i] and gf[i] == gf[i]]
    s = dict(coin=c, quote=q, swaps_all=len(SW[c]), swaps=n0, swaps_clean=len(sw), dropped=dr, gate_trades=len(gp), bins_both=len(ok),
             sigma_before_bp=sig_before, fee_lamports_med=fees[len(fees) // 2] if fees else None)
    if len(ok) < 500: s['status'] = 'skip: мало совпавших корзин'; summ.append(s); print(s, flush=True); continue
    gap = {i: math.log(gf[i] / dg[i]) * 1e4 for i in ok}
    med = statistics.median(gap.values()); sd = statistics.pstdev([v - med for v in gap.values()])
    s.update(gap_median_bp=round(med, 1), sigma_after_bp=round(sd, 1), gap_sd_bp=round(sd, 1), status='ok')
    rnd = random.Random(378 + sum(map(ord, c)))
    for X in (50, 100, 200, 500):
        i = 6; HM = 30  # макс. удержание 30 корзин = 5 мин
        while i < N - HM - 2:
            if i not in gap or abs(gap[i] - med) < X: i += 1; continue
            dev = gap[i] - med; sg = -1 if dev > 0 else 1; te = i + 1
            tc = None
            for u in range(te, te + HM + 1):
                if u in gap and abs(gap[u] - med) <= X / 2: tc = u - te; break
            hold = tc if tc is not None else HM; end = te + hold
            if gf[te] != gf[te] or gf[end] != gf[end]: i += 1; continue
            pnl = sg * math.log(gf[end] / gf[te]) * 1e4
            m = math.log(gf[i] / gf[i - 6]) * 1e4 if gf[i - 6] == gf[i - 6] else 0.0; cs = []; tol = 1.5 if abs(m) < 3 else 0.3 * abs(m)
            if abs(m) > 0.05:
                for _ in range(300):
                    t0 = rnd.randrange(6, N - HM - 3)
                    mc = math.log(gf[t0] / gf[t0 - 6]) * 1e4
                    if mc == mc and gf[t0 + 1] == gf[t0 + 1] and gf[t0 + 1 + hold] == gf[t0 + 1 + hold] and mc * m > 0 and abs(abs(mc) - abs(m)) <= tol:
                        cs.append(sg * math.log(gf[t0 + 1 + hold] / gf[t0 + 1]) * 1e4)
                        if len(cs) >= 10: break
            rows_ep.append(dict(coin=c, X=X, day=15 + i * B // 86400, dev0=round(dev, 1), conv_bins=tc if tc is not None else '', pnl=round(pnl, 1),
                                ctl=round(sum(cs) / len(cs), 1) if cs else '', ctl_n=len(cs), hold_s=hold * B))
            i = end + 1
    summ.append(s); print(s, flush=True)
json.dump(summ, open(f'{OUT}/summ.json', 'w'), ensure_ascii=False, indent=1)
if rows_ep:
    w = csv.DictWriter(open(f'{OUT}/episodes.csv', 'w', newline=''), list(rows_ep[0])); w.writeheader(); w.writerows(rows_ep)
print('done', len(rows_ep))
