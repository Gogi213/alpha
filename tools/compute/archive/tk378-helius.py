#!/usr/bin/env python3
"""TK-378: свопы Solana через Helius RPC (getSignaturesForAddress -> getTransaction) по пулам TK-380.

Порядок (CEO 23.10 18:58): P1 — пулы монет Gate «без тяжеловесов» (6 пулов, дни 15–22.10), KIMCHI последним в P1;
P2 — остальные пулы с txns24 <= 5000, дни 16, 19, 22.10 по очереди. Жёсткий потолок кредитов (1 кредит = 1 вызов).
Ключ — env HELIUS_API_KEY (значение не печатается). Возобновляемо: уже снятые подписи пропускаются.
Выход: <out>/tx-<pool>.jsonl (компактный tx), <out>/sigs-<pool>.json, <out>/status.json, метка <out>/helius.rc.
"""
import calendar, csv, json, os, sys, time, threading, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

pools_csv, mints_csv, out, cap = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
RPS = 9.0
T0 = calendar.timegm(time.strptime('2026-10-15', '%Y-%m-%d'))  # 15.10 00:00 UTC
DAY = 86400
URL = 'https://mainnet.helius-rpc.com/?api-key=' + os.environ['HELIUS_API_KEY']
os.makedirs(out, exist_ok=True)

lock = threading.Lock()
credits = 0
last_t = [0.0]


def rpc(method, params):
    global credits
    for att in range(8):
        with lock:
            now = time.time()
            wait = last_t[0] + 1.0 / RPS - now
            if wait > 0:
                time.sleep(wait)
            last_t[0] = time.time()
            if credits >= cap:
                raise SystemExit('cap')
            credits += 1
        try:
            r = urllib.request.Request(URL, json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}).encode(),
                                       {'Content-Type': 'application/json'})
            d = json.loads(urllib.request.urlopen(r, timeout=60).read())
            if 'error' in d:
                if d['error'].get('code') == 429:
                    time.sleep(2 + att * 2)
                    continue
                raise RuntimeError(str(d['error'])[:120])
            return d['result']
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2 + att * 3)
                continue
            time.sleep(1 + att)
        except Exception:
            time.sleep(1 + att)
    return None


def day_of(bt):
    return (bt - T0) // DAY  # 0 = 15.10


def get_sigs(pool):
    f = f'{out}/sigs-{pool}.json'
    if os.path.exists(f):
        return json.load(open(f))
    sigs, before, calls = [], None, 0
    while True:
        calls += 1
        if calls > 150:  # >150k подписей: пул забит чужими tx, помечаем обрезку
            sigs.append(['TRUNC', 0])
            break
        p = {'limit': 1000}
        if before:
            p['before'] = before
        res = rpc('getSignaturesForAddress', [pool, p])
        if not res:
            break
        for s in res:
            if s.get('blockTime') is not None and s['blockTime'] >= T0 and s.get('err') is None:
                sigs.append([s['signature'], s['blockTime']])
        before = res[-1]['signature']
        if res[-1].get('blockTime') is not None and res[-1]['blockTime'] < T0:
            break
        if len(res) < 1000:
            break
    json.dump(sigs, open(f + '.tmp', 'w'))
    os.replace(f + '.tmp', f)
    return sigs


def compact(sig, res):
    m, t = res['meta'], res['transaction']
    keys = t['message']['accountKeys']
    pids = [keys[i['programIdIndex']] for i in t['message']['instructions']]
    def tb(L):
        return [[b['accountIndex'], b['mint'], b.get('owner'), b['uiTokenAmount']['amount'], b['uiTokenAmount']['decimals']] for b in L]
    return {'s': sig, 'slot': res['slot'], 'idx': res.get('transactionIndex'), 't': res['blockTime'], 'fee': m['fee'],
            'ns': t['message']['header']['numRequiredSignatures'], 'signer': keys[0], 'cu': m.get('computeUnitsConsumed'),
            'pids': pids, 'pre': tb(m.get('preTokenBalances') or []), 'post': tb(m.get('postTokenBalances') or []),
            'sol_pre': m['preBalances'], 'sol_post': m['postBalances'], 'keys': keys}


def run_pool(pool, days, label):
    sigs = get_sigs(pool)
    want = [s for s in sigs if day_of(s[1]) in days]
    f = f'{out}/tx-{pool}.jsonl'
    have = set()
    if os.path.exists(f):
        for l in open(f):
            try:
                have.add(json.loads(l)['s'])
            except Exception:
                pass
    todo = [s for s in want if s[0] not in have]
    with open(f, 'a') as fo, ThreadPoolExecutor(8) as ex:
        def one(s):
            res = rpc('getTransaction', [s[0], {'maxSupportedTransactionVersion': 0, 'encoding': 'json'}])
            return compact(s[0], res) if res else None
        for c in ex.map(one, todo):
            if c:
                fo.write(json.dumps(c) + '\n')
        fo.flush()
    status(label, pool, len(want), len(todo))


def status(label, pool, nwant, ntodo):
    with lock:
        json.dump({'credits': credits, 'cap': cap, 'last': [label, pool, nwant, ntodo], 'ts': time.strftime('%F %T')},
                  open(f'{out}/status.json', 'w'))


gm = {l.split(',')[2] for l in open(mints_csv).read().split()[1:] if l.startswith('gate,')}
rows = [x for x in csv.DictReader(open(pools_csv, encoding='utf-8')) if x['chain'] == 'solana']
p1 = sorted([x for x in rows if x['token'] in gm], key=lambda x: int(float(x['txns24'])))
p2 = [x for x in rows if x['token'] not in gm and int(float(x['txns24'])) <= 5000]
if len(sys.argv) > 5 and sys.argv[5] == 'sigs':
    with open(f'{out}/sigcounts.csv', 'w') as fc:
        fc.writelines(['pool,coin,txns24,trunc,', ','.join(f'd{i}' for i in range(8)), chr(10)])
        for x in p1 + p2:
            sg = get_sigs(x['pool'])
            c = [sum(1 for q in sg if q[0] != 'TRUNC' and day_of(q[1]) == i) for i in range(8)]
            tr = int(any(q[0] == 'TRUNC' for q in sg))
            fc.writelines([f"{x['pool']},{x['coin']},{x['txns24']},{tr},", ','.join(map(str, c)), chr(10)])
            fc.flush()
    open(f'{out}/sigs.rc', 'w').write('0' + chr(10))
    sys.exit(0)
try:
    for x in p1:
        run_pool(x['pool'], set(range(8)), 'P1')
    for d in (1, 4, 7):  # 16, 19, 22.10
        for x in p2:
            run_pool(x['pool'], {d}, f'P2d{d}')
    open(f'{out}/helius.rc', 'w').write('0\n')
except SystemExit:
    open(f'{out}/helius.rc', 'w').write('cap\n')
status('end', '', 0, 0)
