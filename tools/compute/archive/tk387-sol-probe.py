#!/usr/bin/env python3
"""TK-387: проба Solana gTFA — 1 пул × 1 сутки UTC, число успешных tx и запросов (10 кредитов/запрос). Ключ — env HELIUS_API_KEY.
argv: pool YYYY-MM-DD [max_requests]. Печатает tx, запросы, кредиты (≈), сек."""
import datetime as dt, json, os, sys, time, urllib.request
pool, day = sys.argv[1], sys.argv[2]; cap = int(sys.argv[3]) if len(sys.argv) > 3 else 200
a = int(dt.datetime.fromisoformat(day).replace(tzinfo=dt.timezone.utc).timestamp()); b = a + 86400
URL = 'https://mainnet.helius-rpc.com/?api-key=' + os.environ['HELIUS_API_KEY']
n = req = 0; tok = None; t0 = time.time()
while req < cap:
    cfg = {'transactionDetails': 'full', 'sortOrder': 'asc', 'limit': 100, 'maxSupportedTransactionVersion': 1,
           'filters': {'blockTime': {'gte': a, 'lt': b}, 'status': 'succeeded'}}
    if tok: cfg['paginationToken'] = tok
    r = urllib.request.Request(URL, json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'getTransactionsForAddress', 'params': [pool, cfg]}).encode(), {'Content-Type': 'application/json', 'User-Agent': 'alpha-research'})
    d = json.loads(urllib.request.urlopen(r, timeout=90).read()); req += 1
    if 'error' in d: print('error', str(d['error'])[:200]); break
    res = d['result']; n += len(res.get('data', [])); tok = res.get('paginationToken')
    if not tok or not res.get('data'): break
print(f'{pool} {day}: tx={n} req={req} credits~{req*10} sec={time.time()-t0:.1f} capped={req>=cap}')
