#!/usr/bin/env python3
"""TK-387 (G-016): свопы Solana через Helius getTransactionsForAddress — дневные срезы UTC от листинга на Gate до 14.10.

argv: pools.csv(coin,pool,start_day YYYY-MM-DD) out cap_credits [threads=8]. Окно: [max(01.09, start_day) 00:00, 15.10 00:00) UTC.
Задача = (пул, сутки): <out>/<coin>/tx-<pool>-<YYYYMMDD>.jsonl.gz (формат compact как tk380-sol-gtfa2: полные успешные tx).
Возобновляемо (готовый файл пропускается). Порядок: пулы по возрастанию оценки объёма (порядок строк pools.csv), сутки по возрастанию.
Кредиты — 10 за запрос; на потолке cap — остановка (rc 'cap'). Ключ — env HELIUS_API_KEY (значение не печатается).
Выход: <out>/DONE.json {rc, tasks, done, credits_assumed, requests, per_pool}."""
import csv, datetime as dt, gzip, json, os, sys, threading, time, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

pools_csv, out, cap = sys.argv[1], sys.argv[2], int(sys.argv[3])
threads = int(sys.argv[4]) if len(sys.argv) > 4 else 8
T0 = 1788220800   # 01.09 00:00 UTC
T1 = 1792022400   # 15.10 00:00 UTC
URL = 'https://mainnet.helius-rpc.com/?api-key=' + os.environ['HELIUS_API_KEY']
PER_REQ = 10
lock = threading.Lock()
credits = [0]
nreq = [0]
stop = threading.Event()


class Cap(Exception):
    pass


def call(params):
    for att in range(14):
        if stop.is_set():
            raise Cap()
        with lock:
            if credits[0] + PER_REQ > cap:
                stop.set()
                raise Cap()
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
            time.sleep(min(30, 2 + att * 3 if e.code == 429 else 1 + att * 2))
        except RuntimeError:
            raise
        except Exception:
            time.sleep(min(30, 1 + att * 2))
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


def run_day(coin, pool, a, path):
    n, tok = 0, None
    tmp = path + '.tmp'
    with gzip.open(tmp, 'wt') as fo:
        while True:
            cfg = {'transactionDetails': 'full', 'sortOrder': 'asc', 'limit': 100, 'maxSupportedTransactionVersion': 1,
                   'filters': {'blockTime': {'gte': a, 'lt': a + 86400}, 'status': 'succeeded'}}
            if tok:
                cfg['paginationToken'] = tok
            res = call([pool, cfg])
            for x in res.get('data', []):
                fo.write(json.dumps(compact(x)) + '\n')
                n += 1
            tok = res.get('paginationToken')
            if not tok or not res.get('data'):
                break
    os.replace(tmp, path)
    return n


tasks = []
for r in csv.DictReader(open(pools_csv)):
    s = max(T0, int(dt.datetime.fromisoformat(r['start_day']).replace(tzinfo=dt.timezone.utc).timestamp()))
    os.makedirs(f"{out}/{r['coin']}", exist_ok=True)
    for a in range(s, T1, 86400):
        day = dt.datetime.fromtimestamp(a, dt.timezone.utc).strftime('%Y%m%d')
        tasks.append((r['coin'], r['pool'], a, f"{out}/{r['coin']}/tx-{r['pool']}-{day}.jsonl.gz"))
per_pool, done = {}, [0]


def work(t):
    coin, pool, a, path = t
    if os.path.exists(path):
        with lock:
            done[0] += 1
        return
    try:
        n = run_day(coin, pool, a, path)
    except Cap:
        return
    with lock:
        done[0] += 1
        per_pool[coin] = per_pool.get(coin, 0) + n
        json.dump({'done': done[0], 'tasks': len(tasks), 'credits_assumed': credits[0], 'requests': nreq[0], 'last': [coin, a, n], 'ts': time.strftime('%F %T')},
                  open(f'{out}/status.json', 'w'))


rc = 'ok'
try:
    with ThreadPoolExecutor(threads) as ex:
        list(ex.map(work, tasks))
except Exception as e:
    rc = 'error: ' + str(e)[:150]
if stop.is_set():
    rc = 'cap'
json.dump({'rc': rc, 'tasks': len(tasks), 'done': done[0], 'credits_assumed': credits[0], 'requests': nreq[0], 'per_pool_new_tx': per_pool, 'ts': time.strftime('%F %T')},
          open(f'{out}/DONE.json', 'w'))
sys.exit(0 if rc == 'ok' else 1)
