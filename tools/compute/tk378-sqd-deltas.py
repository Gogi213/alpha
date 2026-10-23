#!/usr/bin/env python3
"""TK-378: SQD-файл (блоки с tokenBalances Gate-монет) -> дельты балансов по tx.
Для tx, где у аккаунта Gate-монеты M ненулевая дельта, пишет все ненулевые дельты M и quote (WSOL/USDC/USDT) + fee/payer/CU.
Запуск: tk378-sqd-deltas.py MINTS.csv IN.jsonl OUT.csv ; конец — OUT.csv.done"""
import sys, csv, json
Q = {'So11111111111111111111111111111111111111112', 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v', 'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB'}
M = {r['mint'] for r in csv.DictReader(open(sys.argv[1])) if r['src'] == 'gate'}
w = csv.writer(open(sys.argv[3], 'w', newline=''))
w.writerow('ts,slot,txidx,sig,payer,fee,cu,mint,account,delta,dec'.split(','))
for line in open(sys.argv[2]):
    d = json.loads(line)
    h = d['header']
    tx = {t['transactionIndex']: t for t in d.get('transactions', [])}
    by = {}
    for b in d.get('tokenBalances', []):
        mint = b.get('postMint') or b.get('preMint')
        dl = int(b.get('postAmount') or 0) - int(b.get('preAmount') or 0)
        if dl and (mint in M or mint in Q):
            by.setdefault(b['transactionIndex'], []).append((mint, b['account'], dl, b.get('postDecimals', b.get('preDecimals'))))
    for ti, rows in by.items():
        if not any(r[0] in M for r in rows): continue
        t = tx.get(ti, {})
        if t.get('err'): continue
        sig = (t.get('signatures') or [''])[0]
        for mint, acc, dl, dec in rows:
            w.writerow([h['timestamp'], h['number'], ti, sig, t.get('feePayer', ''), t.get('fee', ''), t.get('computeUnitsConsumed', ''), mint, acc, dl, dec])
open(sys.argv[3] + '.done', 'w').write('1\n')
