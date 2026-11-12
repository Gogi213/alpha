#!/usr/bin/env python3
"""TK-401 (Судья 25.10 21:25): свопы Solana по ПАРЕ vault'ов (M-vault, Q-vault) одного пула, а не по сумме всех vault'ов.
swaps2 суммировал дельты всех «vault'ов» монеты (top 8 по числу tx с обоими знаками — туда попадали ATA
маршрутизаторов/ботов/комиссий) и ставил в пару квоту другого hop'а: TRUMP 1,9…7552 USDC по одному mint.
Здесь: кандидаты M (>=MINTX tx обоих знаков, top 40) × счета quote в той же tx с противоположным знаком = пара.
Пул = пара, у которой >=MINTX совместных tx и цена tx согласована с ПРЕДЫДУЩИМИ сделками этой же пары
(в пределах TOL от медианы 10 предыдущих, доля >=FRAC; структура пары, не фильтр цены итога). На vault M — лучшая пара.
Своп = tx, где обе ноги выбранной пары ненулевые и противоположного знака; цена = только по этим двум дельтам.
Запуск: tk378-sol-swaps3.py MINTS.csv OUT_swaps.csv OUT_cov.csv dl/*.csv   (env SRC, COINS=список через запятую)"""
import sys, csv, os, math, collections, statistics
csv.field_size_limit(sys.maxsize)
Q = {'So11111111111111111111111111111111111111112': 'WSOL', 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v': 'USDC',
     'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB': 'USDT'}
only = set(os.environ['COINS'].split(',')) if os.environ.get('COINS') else None
coin = {r['mint']: r['coin'] for r in csv.DictReader(open(sys.argv[1])) if r['src'] == os.environ.get('SRC', 'gate') and (only is None or r['coin'] in only)}
files = sys.argv[4:]
MINTX, TOPM, NS, TOL, FRAC = 30, 40, 300, 0.05, 0.8
def rows(f):
    r = csv.reader(open(f)); next(r)
    for ts, slot, ti, sig, payer, fee, cu, mint, acc, d, dec in r:
        yield int(ts), int(slot), int(ti), sig, payer, fee, cu, mint, acc, int(d), (int(dec) if dec else None)
def txs():
    cur, buf = None, []
    for f in files:
        for r in rows(f):
            if r[3] != cur:
                if buf: yield buf
                buf = []; cur = r[3]
            buf.append(r)
    if buf: yield buf
DEC = {}
cnt = collections.defaultdict(lambda: [set(), set()])
for f in files:
    for ts, slot, ti, sig, payer, fee, cu, mint, acc, d, dec in rows(f):
        if dec is not None: DEC[mint] = dec
        if mint in coin: cnt[(mint, acc)][0 if d > 0 else 1].add(sig)
cand = collections.defaultdict(list)
for (m, a), (p, n) in cnt.items():
    if len(p) >= MINTX and len(n) >= MINTX: cand[m].append((len(p) + len(n), a))
cand = {m: {a for _, a in sorted(v, reverse=True)[:TOPM]} for m, v in cand.items()}
del cnt
# проход 2: статистика пар
pairs = collections.defaultdict(lambda: [0, []])  # (m,a,qm,b) -> [n, [price...]]
for buf in txs():
    ms = {}
    for r in buf:
        if r[7] in cand and r[8] in cand[r[7]] and r[9]: ms[(r[7], r[8])] = r[9]
    if not ms: continue
    qs = [(r[7], r[8], r[9]) for r in buf if r[7] in Q and r[9]]
    for (m, a), da in ms.items():
        for qm, b, db in qs:
            if (da > 0) == (db > 0): continue
            p = pairs[(m, a, qm, b)]
            p[0] += 1
            if len(p[1]) < NS: p[1].append(abs(db) / 10 ** DEC[qm] / (abs(da) / 10 ** DEC[m]))
def tight(ps):
    if len(ps) < MINTX: return 0.0
    ok = 0
    for i in range(10, len(ps)):
        med = statistics.median(ps[i - 10:i])
        if abs(math.log(ps[i] / med)) < math.log(1 + TOL): ok += 1
    return ok / (len(ps) - 10)
best = {}
for (m, a, qm, b), (n, ps) in pairs.items():
    if n < MINTX: continue
    t = tight(ps)
    if t < FRAC: continue
    if (m, a) not in best or n > best[(m, a)][0]: best[(m, a)] = (n, qm, b, t)
sel = collections.defaultdict(dict)  # m -> {(a, b): qm}
for (m, a), (n, qm, b, t) in best.items(): sel[m][(a, b)] = qm
w = csv.writer(open(sys.argv[2], 'w', newline=''))
w.writerow('ts,slot,txidx,sig,coin,side,qty_m,quote,qty_q,price_q,payer,fee,cu'.split(','))
cov = collections.defaultdict(collections.Counter)
for buf in txs():
    d = {(r[7], r[8]): r[9] for r in buf if r[9]}
    t = buf[0]
    for m, pr in sel.items():
        for (a, b), qm in pr.items():
            dm, dq = d.get((m, a)), d.get((qm, b))
            if not dm: continue
            cov[m]['tx_vault'] += 1
            if not dq or (dm > 0) == (dq > 0): cov[m]['no_pair'] += 1; continue
            qty_m = abs(dm) / 10 ** DEC[m]; qty_q = abs(dq) / 10 ** DEC[qm]
            w.writerow([t[0], t[1], t[2], t[3], coin[m], 'buy' if dm < 0 else 'sell', qty_m, Q[qm], qty_q, qty_q / qty_m, t[4], t[5], t[6]])
            cov[m]['swap_' + Q[qm]] += 1
with open(sys.argv[3], 'w', newline='') as fo:
    cw = csv.writer(fo); cw.writerow(['coin', 'mint', 'm_vaults', 'q_vaults', 'tx_vault', 'no_or_multi_quote', 'swap_WSOL', 'swap_USDC', 'swap_USDT'])
    for m, c in cov.items():
        cw.writerow([coin[m], m, len(sel[m]), len({b for (_, b) in sel[m]}), c['tx_vault'], c['no_pair'], c['swap_WSOL'], c['swap_USDC'], c['swap_USDT']])
