#!/usr/bin/env python3
"""TK-393: свопы Sui (GraphQL, без ключа) и Tron (TronGrid, без ключа) за 15–22.10.2026 по пулам pools-old/new.csv.
Использование: tk393-sui-tron.py sui|tron <pools-old.csv> <pools-new.csv> <outdir>. Выход: <outdir>/<chain>/<pool>.jsonl(.gz нет), DONE-<pool>, summary.json.
Sui: transactions(filter:{affectedObject:pool}) от конца назад до 15.10; в файл — события типов *Swap* + digest + timestamp.
Tron: /v1/contracts/{pool}/events за окно (все события) + сырые логи tx через /wallet/gettransactioninfobyid для Swap/Sync."""
import calendar, csv, json, os, sys, time, urllib.request, urllib.error, concurrent.futures as cf
chain, po, pn, out = sys.argv[1:5]
T0, T1 = 1792022400, 1792713599
pools = {}
for f in (po, pn):
    for r in csv.DictReader(open(f, encoding='utf-8')):
        if r['chain'] == chain: pools[r['pool']] = r['dex']
d = os.path.join(out, chain); os.makedirs(d, exist_ok=True)
def http(url, data=None, tries=40):
    for k in range(tries):
        try:
            rq = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json', 'User-Agent': 'curl/8'})
            with urllib.request.urlopen(rq, timeout=90) as f: return json.loads(f.read())
        except urllib.error.HTTPError as e:
            time.sleep(5 + 2 * k if e.code in (429, 502, 503, 504) else 20)
        except Exception: time.sleep(5)
    raise SystemExit('no response ' + url)
Q = 'query($p:String!,$b:String){transactions(last:50,before:$b,filter:{affectedObject:$p}){nodes{digest effects{timestamp events{nodes{contents{type{repr} json}}}}} pageInfo{hasPreviousPage startCursor}}}'
def sui(pool):
    fn = os.path.join(d, pool + '.jsonl'); cf_ = os.path.join(d, pool + '.cursor')
    if os.path.exists(os.path.join(d, 'DONE-' + pool)): return pool, -1
    cur = open(cf_).read().strip() if os.path.exists(cf_) else None
    n = 0; stop = False
    with open(fn, 'a', encoding='utf-8') as w:
        while not stop:
            j = http('https://graphql.mainnet.sui.io/graphql', json.dumps({'query': Q, 'variables': {'p': pool, 'b': cur}}).encode())
            t = j['data']['transactions']
            for nd in reversed(t['nodes']):
                ts = calendar.timegm(time.strptime(nd['effects']['timestamp'][:19], '%Y-%m-%dT%H:%M:%S'))
                if ts < T0: stop = True; continue
                if ts > T1: continue
                ev = [e['contents'] for e in nd['effects']['events']['nodes'] if 'wap' in e['contents']['type']['repr']]
                if ev: w.write(json.dumps({'d': nd['digest'], 't': ts, 'ev': ev}) + '\n'); n += 1
            if not t['pageInfo']['hasPreviousPage']: break
            cur = t['pageInfo']['startCursor']; open(cf_, 'w').write(cur)
    open(os.path.join(d, 'DONE-' + pool), 'w').write(str(n))
    return pool, n
def tron(pool):
    fn = os.path.join(d, pool + '.jsonl')
    if os.path.exists(os.path.join(d, 'DONE-' + pool)): return pool, -1
    url = f'https://api.trongrid.io/v1/contracts/{pool}/events?limit=200&only_confirmed=true&min_block_timestamp={T0*1000}&max_block_timestamp={T1*1000}&order_by=block_timestamp,asc'
    txs = {}; n = 0
    while url:
        j = http(url); time.sleep(0.4)
        for e in j.get('data', []):
            txs.setdefault(e['transaction_id'], []).append({k: e[k] for k in ('block_timestamp', 'event_name', 'event_index', 'result')})
        url = j.get('meta', {}).get('links', {}).get('next')
    with open(fn, 'w', encoding='utf-8') as w:
        for tx, evs in txs.items():
            if not any(x['event_name'] in ('Swap', 'TokenPurchase', 'TrxPurchase') for x in evs): continue
            info = http('https://api.trongrid.io/wallet/gettransactioninfobyid', json.dumps({'value': tx}).encode()); time.sleep(0.4)
            w.write(json.dumps({'tx': tx, 'ev': evs, 'log': info.get('log', []), 'blk': info.get('blockNumber')}) + '\n'); n += 1
    open(os.path.join(d, 'DONE-' + pool), 'w').write(str(n))
    return pool, n
fn = sui if chain == 'sui' else tron
res = {}
with cf.ThreadPoolExecutor(8 if chain == 'sui' else 2) as ex:
    for p, n in ex.map(fn, sorted(pools)):
        res[p] = n; print(chain, p, pools[p], n, flush=True)
json.dump({'chain': chain, 'pools': len(pools), 'swap_txs': res}, open(os.path.join(d, 'summary.json'), 'w'))
open(os.path.join(out, chain.upper() + 'DONE'), 'w').write('ok')
