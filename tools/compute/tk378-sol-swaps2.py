#!/usr/bin/env python3
"""TK-378: свопы Gate-монет Solana из дельт балансов SQD (dl/*.csv, tk378-sqd-deltas.py).
Vault монеты = M-аккаунты в >=MINTX tx с дельтами обоих знаков (топ 8); Q-vault = quote-аккаунты, чаще всего
двигавшиеся в тех же tx и в обе стороны. Своп = tx, где суммарная дельта vault'ов M и дельта Q-vault'ов одного
quote-mint — противоположных знаков. Цена = quote/M; fee/payer/CU — из tx.
Запуск: tk378-sol-swaps2.py MINTS.csv OUT_swaps.csv OUT_cov.csv dl/*.csv"""
import sys, csv, collections
Q = {'So11111111111111111111111111111111111111112': 'WSOL', 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v': 'USDC',
     'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB': 'USDT'}
coin = {r['mint']: r['coin'] for r in csv.DictReader(open(sys.argv[1])) if r['src'] == 'gate'}
files = sys.argv[4:]
MINTX = 30
def rows(f):
    r = csv.reader(open(f)); next(r)
    for ts, slot, ti, sig, payer, fee, cu, mint, acc, d, dec in r:
        yield int(ts), int(slot), int(ti), sig, payer, fee, cu, mint, acc, int(d), (int(dec) if dec else None)
cnt = collections.defaultdict(lambda: [set(), set()])
DEC = {}
for f in files:
    for ts, slot, ti, sig, payer, fee, cu, mint, acc, d, dec in rows(f):
        if dec is not None: DEC[mint] = dec
        if mint in coin: cnt[(mint, acc)][0 if d > 0 else 1].add(sig)
vault = collections.defaultdict(set)
for (m, a), (p, n) in cnt.items():
    if len(p) >= MINTX and len(n) >= MINTX: vault[m].add((len(p) + len(n), a))
vault = {m: {a for _, a in sorted(v, reverse=True)[:8]} for m, v in vault.items()}
txv = {m: set() for m in vault}
for f in files:
    for ts, slot, ti, sig, payer, fee, cu, mint, acc, d, dec in rows(f):
        if mint in coin and acc in vault.get(mint, ()): txv[mint].add(sig)
qc = collections.defaultdict(lambda: collections.defaultdict(lambda: [set(), set()]))
for f in files:
    for ts, slot, ti, sig, payer, fee, cu, mint, acc, d, dec in rows(f):
        if mint in Q:
            for m in vault:
                if sig in txv[m]: qc[m][(mint, acc)][0 if d > 0 else 1].add(sig)
qv = {}
for m in vault:
    c = [(len(p) + len(n), k) for k, (p, n) in qc[m].items() if len(p) >= MINTX and len(n) >= MINTX]
    qv[m] = {k for _, k in sorted(c, reverse=True)[:8]}
w = csv.writer(open(sys.argv[2], 'w', newline=''))
w.writerow('ts,slot,txidx,sig,coin,side,qty_m,quote,qty_q,price_q,payer,fee,cu'.split(','))
cov = collections.defaultdict(collections.Counter)
buf = []
def flush():
    if not buf: return
    sig = buf[0][3]
    for m in vault:
        dm = sum(r[9] for r in buf if r[7] == m and r[8] in vault[m])
        if not dm: continue
        cov[m]['tx_vault'] += 1
        qs = collections.defaultdict(int)
        for r in buf:
            if (r[7], r[8]) in qv[m]: qs[r[7]] += r[9]
        qs = {k: v for k, v in qs.items() if v and (v > 0) != (dm > 0)}
        if len(qs) != 1: cov[m]['no_or_multi_quote'] += 1; continue
        (qm, dq), = qs.items()
        dm_dec = DEC[m]; dq_dec = DEC[qm]
        price = abs(dq) / 10 ** dq_dec / (abs(dm) / 10 ** dm_dec)
        t = buf[0]
        w.writerow([t[0], t[1], t[2], sig, coin[m], 'buy' if dm < 0 else 'sell', abs(dm) / 10 ** dm_dec, Q[qm], abs(dq) / 10 ** dq_dec, price, t[4], t[5], t[6]])
        cov[m]['swap_' + Q[qm]] += 1
cur = None
for f in files:
    for r in rows(f):
        if r[3] != cur:
            flush(); buf = []; cur = r[3]
        buf.append(r)
flush()
with open(sys.argv[3], 'w', newline='') as fo:
    cw = csv.writer(fo); cw.writerow(['coin', 'mint', 'm_vaults', 'q_vaults', 'tx_vault', 'no_or_multi_quote', 'swap_WSOL', 'swap_USDC', 'swap_USDT'])
    for m, c in cov.items():
        cw.writerow([coin[m], m, len(vault[m]), len(qv[m]), c['tx_vault'], c['no_or_multi_quote'], c['swap_WSOL'], c['swap_USDC'], c['swap_USDT']])
