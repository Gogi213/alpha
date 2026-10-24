#!/usr/bin/env python3
"""TK-380: свопы Solana через Helius getTransactionsForAddress (полные tx, фильтр blockTime), пулы из sigcounts TK-378.

argv: sigcounts.csv out cap_credits max_tx_per_pool [pool-list-csv(coverage)]. Кредиты считаются как 10 за запрос (доки Helius; сверить по панели).
Порядок: по возрастанию числа tx пула; пул с числом tx > max_tx_per_pool пропускается (строкой в skipped.csv).
Выход: <out>/tx-<pool>.jsonl.gz (compact как tk378-helius), <out>/status.json, <out>/DONE.json. Возобновляемо (готовый пул пропускается).
Ключ — env HELIUS_API_KEY."""
import csv, gzip, json, os, sys, time, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
import threading

sigc, out, cap, maxtx = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
T0, T1 = 1792022400, 1792022400 + 8 * 86400  # 15.10 00:00 — 23.10 00:00 UTC
URL = 'https://mainnet.helius-rpc.com/?api-key=' + os.environ['HELIUS_API_KEY']
PER_REQ = 10
os.makedirs(out, exist_ok=True)
lock = threading.Lock()
credits = [0]
nreq = [0]


def call(params):
    for att in range(8):
        with lock:
            if credits[0] + PER_REQ > cap:
                raise SystemExit('cap')
            credits[0] += PER_REQ
            nreq[0] += 1
        try:
            r = urllib.request.Request(URL, json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'getTransactionsForAddress', 'params': params}).encode(),
                                       {'Content-Type': 'application/json', 'User-Agent': 'alpha-research'})
            d = json.loads(urllib.request.urlopen(r, timeout=90).read())
            if 'error' in d:
                if d['error'].get('code') == 429:
                    time.sleep(2 + att * 2)
                    continue
                raise RuntimeError(str(d['error'])[:150])
            return d['result']
        except urllib.error.HTTPError as e:
            time.sleep(2 + att * 3 if e.code == 429 else 1 + att)
        except RuntimeError:
            raise
        except Exception:
            time.sleep(1 + att)
    raise RuntimeError('retries')


def compact(res):
    m, t = res['meta'], res['transaction']
    keys = t['message']['accountKeys']
    keys = [k if isinstance(k, str) else k['pubkey'] for k in keys]
    pids = [keys[i['programIdIndex']] for i in t['message']['instructions']]
    def tb(L):
        return [[b['accountIndex'], b['mint'], b.get('owner'), b['uiTokenAmount']['amount'], b['uiTokenAmount']['decimals']] for b in L]
    return {'s': t['signatures'][0], 'slot': res['slot'], 'idx': res.get('transactionIndex'), 't': res['blockTime'], 'fee': m['fee'],
            'ns': t['message']['header']['numRequiredSignatures'], 'signer': keys[0], 'cu': m.get('computeUnitsConsumed'),
            'pids': pids, 'pre': tb(m.get('preTokenBalances') or []), 'post': tb(m.get('postTokenBalances') or []),
            'sol_pre': m['preBalances'], 'sol_post': m['postBalances'], 'keys': keys}


def pool_slices(pool, total):
    """Фронтир по времени: окно 15–23.10 режем на N частей по числу tx, чтобы идти параллельно."""
    n = max(1, min(8, total // 3000))
    step = (T1 - T0) // n
    return [(T0 + i * step, T0 + (i + 1) * step if i < n - 1 else T1) for i in range(n)]


def run_slice(pool, a, b):
    rows, tok = [], None
    while True:
        cfg = {'transactionDetails': 'full', 'sortOrder': 'asc', 'limit': 100, 'maxSupportedTransactionVersion': 1,
               'filters': {'blockTime': {'gte': a, 'lt': b}, 'status': 'succeeded'}}
        if tok:
            cfg['paginationToken'] = tok
        res = call([pool, cfg])
        for x in res.get('data', []):
            rows.append(compact(x))
        tok = res.get('paginationToken')
        if not tok or not res.get('data'):
            break
    return rows


rows = list(csv.DictReader(open(sigc)))
for r in rows:
    r['tot'] = sum(int(r['d%d' % i]) for i in range(8))
rows.sort(key=lambda r: r['tot'])
skipped, done = [], 0
try:
    for r in rows:
        pool = r['pool']
        f = f'{out}/tx-{pool}.jsonl.gz'
        if os.path.exists(f):
            done += 1
            continue
        if r['trunc'] == '1' or r['tot'] > maxtx:
            skipped.append((pool, r['coin'], r['tot'], r['trunc']))
            continue
        sl = pool_slices(pool, r['tot'])
        with ThreadPoolExecutor(len(sl)) as ex:
            parts = list(ex.map(lambda ab: run_slice(pool, *ab), sl))
        with gzip.open(f + '.tmp', 'wt') as fo:
            for p in parts:
                for c in p:
                    fo.write(json.dumps(c) + '\n')
        os.replace(f + '.tmp', f)
        done += 1
        json.dump({'pools_done': done, 'credits_assumed': credits[0], 'requests': nreq[0], 'cap': cap, 'last': [r['coin'], pool, r['tot'], sum(map(len, parts))], 'ts': time.strftime('%F %T')},
                  open(f'{out}/status.json', 'w'))
    rc = 'ok'
except SystemExit:
    rc = 'cap'
with open(f'{out}/skipped.csv', 'w') as fs:
    fs.write('pool,coin,tot,trunc\n')
    for s in skipped:
        fs.write(','.join(map(str, s)) + '\n')
json.dump({'rc': rc, 'pools_done': done, 'skipped': len(skipped), 'credits_assumed': credits[0], 'requests': nreq[0], 'ts': time.strftime('%F %T')}, open(f'{out}/DONE.json', 'w'))
