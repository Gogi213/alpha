#!/usr/bin/env python3
"""TK-410 long4: свопы Sui-пулов по сегментам контрольных точек параллельно (вместо одного прохода назад от конца).
Использование: tk410-sui-seg.py <outdir> <t0> <t1> <pool>:<K>[:<t1pool>] ...  Выход <outdir>/<pool>.<k>.jsonl ({d,t,ev}), метка DONE-<pool>.<k>.
Сегмент = диапазон контрольных точек (afterCheckpoint/beforeCheckpoint), страницы last:50 назад; докачка — файл .cursor."""
import calendar, json, os, sys, time, urllib.request, urllib.error, concurrent.futures as cf
out, T0, T1 = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]); os.makedirs(out, exist_ok=True)
U = 'https://graphql.mainnet.sui.io/graphql'
def g(q, v=None):
    for k in range(60):
        try:
            rq = urllib.request.Request(U, data=json.dumps({'query': q, 'variables': v or {}}).encode(), headers={'Content-Type': 'application/json', 'User-Agent': 'curl/8'})
            with urllib.request.urlopen(rq, timeout=90) as f:
                j = json.loads(f.read())
            if j.get('errors') and not j.get('data'): raise SystemExit('gql ' + str(j['errors'])[:300])
            return j['data']
        except urllib.error.HTTPError as e: time.sleep(5 + 2 * min(k, 10) if e.code in (429, 502, 503, 504) else 20)
        except SystemExit: raise
        except Exception: time.sleep(5)
    raise SystemExit('no response')
def ts_of(n):
    s = g('query($n:UInt53){checkpoint(sequenceNumber:$n){timestamp}}', {'n': n})['checkpoint']
    return calendar.timegm(time.strptime(s['timestamp'][:19], '%Y-%m-%dT%H:%M:%S')) if s else None
head = int(g('{checkpoint{sequenceNumber}}')['checkpoint']['sequenceNumber'])
def cp_at(t):   # первая точка с ts >= t
    lo, hi = 1, head
    while lo < hi:
        m = (lo + hi) // 2; x = ts_of(m)
        if x is None or x < t: lo = m + 1
        else: hi = m
    return lo
Q = 'query($p:String!,$b:String,$a:UInt53,$e:UInt53){transactions(last:50,before:$b,filter:{affectedObject:$p,afterCheckpoint:$a,beforeCheckpoint:$e}){nodes{digest effects{timestamp events{nodes{contents{type{repr} json}}}}} pageInfo{hasPreviousPage startCursor}}}'
def seg(pool, k, a, e):
    fn = f'{out}/{pool}.{k}.jsonl'; cfn = f'{out}/{pool}.{k}.cursor'; done = f'{out}/DONE-{pool}.{k}'
    if os.path.exists(done): return pool, k, -1
    cur = open(cfn).read().strip() if os.path.exists(cfn) else None
    n = 0
    with open(fn, 'a', encoding='utf-8') as w:
        while True:
            t = g(Q, {'p': pool, 'b': cur, 'a': a, 'e': e})['transactions']
            for nd in reversed(t['nodes']):
                ts = calendar.timegm(time.strptime(nd['effects']['timestamp'][:19], '%Y-%m-%dT%H:%M:%S'))
                ev = [x['contents'] for x in nd['effects']['events']['nodes'] if 'wap' in x['contents']['type']['repr']]
                if ev: w.write(json.dumps({'d': nd['digest'], 't': ts, 'ev': ev}) + '\n'); n += 1
            w.flush()
            if not t['pageInfo']['hasPreviousPage']: break
            cur = t['pageInfo']['startCursor']; open(cfn, 'w').write(cur)
    open(done, 'w').write(str(n)); print(pool[:10], k, a, e, n, flush=True)
    return pool, k, n
jobs = []
for spec in sys.argv[4:]:
    p = spec.split(':'); pool, K = p[0], int(p[1]); t1 = int(p[2]) if len(p) > 2 else T1
    a0, a1 = cp_at(T0), cp_at(t1 + 1); print(pool[:10], 'cp', a0, a1, flush=True)
    step = (a1 - a0) // K + 1
    for k in range(K): jobs.append((pool, k, a0 + k * step - 1, min(a1, a0 + (k + 1) * step)))   # after = a-1 (включая a), before = e
with cf.ThreadPoolExecutor(len(jobs)) as ex:
    for f in cf.as_completed([ex.submit(seg, *j) for j in jobs]): f.result()
print('ALL DONE', flush=True)
