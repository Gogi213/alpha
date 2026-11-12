#!/usr/bin/env python3
"""TK-378: свопы Solana через SQD Portal (без ключа): tokenBalances по mint монет Gate, сутки 15–22.10.

Слоты: ~0,18 с (интерполяция по timestamp). Портал отдаёт 529 «overloaded» вперемешку — повтор с паузой.
Выход: <out>/sqd-<COIN>-d<N>.jsonl (сырой поток), <out>/status.txt, метка <out>/sqd.rc. Возобновляемо.
Запуск: tk378-sqd.py mints.csv out [coin,coin]
"""
import csv, json, os, sys, time, urllib.request, urllib.error

mints_csv, out = sys.argv[1], sys.argv[2]
only = set(sys.argv[3].split(',')) if len(sys.argv) > 3 else None
U = 'https://portal.sqd.dev/datasets/solana-mainnet/stream'
H = {'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'}
T0 = 1792022400  # 15.10.2026 00:00 UTC
os.makedirs(out, exist_ok=True)


def post(b, tries=40):
    for a in range(tries):
        try:
            return urllib.request.urlopen(urllib.request.Request(U, json.dumps(b).encode(), H), timeout=300).read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 503, 529):
                time.sleep(min(5 + 3 * a, 30))
                continue
            raise RuntimeError(f'{e.code} {e.read()[:200]}')
        except Exception:
            time.sleep(5)
    raise RuntimeError('retries')


def ts(n):
    r = post({'type': 'solana', 'fromBlock': n, 'toBlock': n + 3000, 'fields': {'block': {'number': True, 'timestamp': True}}})
    j = json.loads(r.decode().split('\n')[0])['header']
    return j['number'], j['timestamp']


def slot_at(t, n=459050000, x=1792460086):
    for _ in range(8):
        n, x = ts(n - int((x - t) / 0.18))
        if abs(x - t) < 30:
            break
    return n


rows = [r for r in csv.DictReader(open(mints_csv)) if r['src'] == 'gate' and (only is None or r['coin'] in only)]
slots = {}
for d in range(9):
    slots[d] = slot_at(T0 + d * 86400)
    open(f'{out}/slots.json', 'w').write(json.dumps(slots))
F = {'block': {'number': True, 'timestamp': True},
     'transaction': {'signatures': True, 'transactionIndex': True, 'feePayer': True, 'fee': True, 'computeUnitsConsumed': True, 'err': True},
     'tokenBalance': {'transactionIndex': True, 'account': True, 'preMint': True, 'postMint': True, 'preOwner': True, 'postOwner': True,
                      'preAmount': True, 'postAmount': True, 'preDecimals': True, 'postDecimals': True}}
for r in rows:
    for d in range(8):
        f = f"{out}/sqd-{r['coin']}-d{d}.jsonl"
        if os.path.exists(f):
            continue
        b = {'type': 'solana', 'fromBlock': slots[d], 'toBlock': slots[d + 1] - 1, 'fields': F,
             'tokenBalances': [{'preMint': [r['mint']], 'transaction': True, 'transactionTokenBalances': True},
                               {'postMint': [r['mint']], 'transaction': True, 'transactionTokenBalances': True}]}
        t = time.time()
        # поток может обрываться по диапазону: докачка с последнего блока
        buf, frm = [], slots[d]
        while frm < slots[d + 1]:
            b['fromBlock'] = frm
            data = post(b).decode().split('\n')
            data = [l for l in data if l]
            if not data:
                break
            buf += data
            last = json.loads(data[-1])['header']['number']
            if last + 1 <= frm:
                break
            frm = last + 1
            if last >= slots[d + 1] - 2:
                break
        open(f + '.tmp', 'w').write('\n'.join(buf) + '\n')
        os.replace(f + '.tmp', f)
        open(f'{out}/status.txt', 'w').write(f"{time.strftime('%F %T')} {r['coin']} d{d} lines={len(buf)} {time.time()-t:.0f}s\n")
open(f'{out}/sqd.rc', 'w').write('0\n')
