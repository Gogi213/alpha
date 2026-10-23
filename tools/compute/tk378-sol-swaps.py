#!/usr/bin/env python3
"""TK-378: свопы Solana из переводов. Vault монеты M = токен-аккаунт, участвующий в переводах M в >=MINTX разных tx
(и как получатель, и как отправитель). Quote-vault = аккаунт, чаще всего противоположно двигающий quote в тех же tx.
Своп = tx, где vault V монеты двигает M, и в той же tx есть quote-перевод через парный Q-vault в обратную сторону.
Запуск: tk378-sol-swaps.py MINTS.csv sol-transfers.csv OUT_swaps.csv OUT_cov.csv"""
import sys, csv, collections
Q = {'So11111111111111111111111111111111111111112': 'WSOL', 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v': 'USDC',
     'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB': 'USDT'}
coin = {r['mint']: (r['src'], r['coin']) for r in csv.DictReader(open(sys.argv[1]))}
bytx = collections.defaultdict(list)
for x in csv.DictReader(open(sys.argv[2])):
    bytx[x['tx_signature']].append(x)
# по монете: аккаунт -> набор tx, где он получал / отдавал M
rin = collections.defaultdict(lambda: collections.defaultdict(set)); rout = collections.defaultdict(lambda: collections.defaultdict(set))
for s, rows in bytx.items():
    for x in rows:
        m = x['mint']
        if m in coin and x['value']:
            rin[m][x['destination']].add(s); rout[m][x['source']].add(s)
MINTX = 20
vaults = {}
for m in coin:
    vs = []
    for a in set(rin[m]) | set(rout[m]):
        i, o = len(rin[m].get(a, ())), len(rout[m].get(a, ()))
        if i >= MINTX and o >= MINTX: vs.append((i + o, a))
    vaults[m] = {a for _, a in sorted(vs, reverse=True)[:6]}
# парный Q-vault: по каждому (V, tx) собираем quote-аккаунты, двигавшиеся в обратную сторону
pair = collections.defaultdict(collections.Counter)
def legs(s, V, m):
    mv = [x for x in bytx[s] if x['mint'] == m and x['value'] and (x['source'] == V or x['destination'] == V)]
    return mv
for m in coin:
    for V in vaults[m]:
        for s in rin[m][V] | rout[m][V]:
            for x in bytx[s]:
                if x['mint'] in Q and x['value']:
                    pair[(m, V)][x['source']] += 1; pair[(m, V)][x['destination']] += 1
# Q-vault = самый частый quote-аккаунт пары, не являющийся «кошельком» (встречается в >= 50% tx вольта)
out = []; cov = collections.defaultdict(lambda: collections.Counter())
for m, (src, c) in coin.items():
    for V in vaults[m]:
        txs = rin[m][V] | rout[m][V]
        qv = [a for a, n in pair[(m, V)].most_common(3) if n >= 0.5 * len(txs)]
        if not qv: cov[c]['vault_no_qvault'] += 1; continue
        Qv = qv[0]
        for s in txs:
            mv = [x for x in bytx[s] if x['mint'] == m and x['value'] and V in (x['source'], x['destination'])]
            qq = [x for x in bytx[s] if x['mint'] in Q and x['value'] and Qv in (x['source'], x['destination'])]
            if not mv or not qq: cov[c]['tx_no_pair'] += 1; continue
            dm = sum((1 if x['destination'] == V else -1) * int(x['value']) / 10 ** int(float(x['decimals'])) for x in mv)  # + = пул получил M (продажа M)
            dq = sum((1 if x['destination'] == Qv else -1) * int(x['value']) / 10 ** int(float(x['decimals'])) for x in qq)
            if dm == 0 or dq == 0 or (dm > 0) == (dq > 0): cov[c]['tx_bad_dir'] += 1; continue
            qm = Q[qq[0]['mint']]
            out.append((mv[0]['block_timestamp'], c, src, m, V, Qv, qm, s, 'sell' if dm > 0 else 'buy', abs(dm), abs(dq), abs(dq) / abs(dm)))
            cov[c]['swaps'] += 1
out.sort()
w = csv.writer(open(sys.argv[3], 'w', newline=''))
w.writerow('ts,coin,src,mint,vault,qvault,quote,tx,side,amt_m,amt_q,price_q'.split(','))
w.writerows(out)
w = csv.writer(open(sys.argv[4], 'w', newline=''))
w.writerow(['coin', 'src', 'vaults', 'swaps', 'txs_total', 'tx_no_pair', 'tx_bad_dir', 'vault_no_qvault'])
for m, (src, c) in coin.items():
    ntx = len(set().union(*[rin[m][a] | rout[m][a] for a in rin[m]], *[()]) if rin[m] else ())
    w.writerow([c, src, len(vaults[m]), cov[c]['swaps'], ntx, cov[c]['tx_no_pair'], cov[c]['tx_bad_dir'], cov[c]['vault_no_qvault']])
