#!/usr/bin/env python3
"""TK-393: свопы EVM-пулов нового чейна через публичный RPC eth_getLogs (без ключа) за 15–22.10.2026 UTC.
Использование: tk393-rpc-logs.py <rpc_url> <pools.csv> <chain> <outdir>. Адресные пулы (42 знака) — фильтр address+топики Swap v2/v3/Pancake/Solidly;
v4-пулы (66 знаков, id в topic1) — фильтр topic0=Swap v4, topic1=ids (RPC требует окно ≤ 30000 блоков без address).
Выход <outdir>/<chain>/logs-<N>.jsonl (лог + ts блока), cursor, DONE.json. Докачка с cursor."""
import csv, json, os, sys, time, urllib.request, urllib.error
url, pcsv, chain, out = sys.argv[1:5]
T0, T1 = int(os.environ.get('T0', 1792022400)), 1792713599
SW = ['0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822',   # v2
      '0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67',   # uni v3
      '0x19b47279256b2a23a1665c810c8d55a1758940ee09377d4f8d26497a3577dc83',   # pancake v3
      '0xb3e2773606abfd36b5bd91394b3a54d1398336c65005baf7bf7a05efeffaf75b']   # solidly/aero v2 Swap
SW4 = '0x40e9cecb9f5f1f1c5b9c97dec2917b7ee92e57ba5563708daca94dd84ad7112f'
rows = [r for r in csv.DictReader(open(pcsv, encoding='utf-8')) if r['chain'] == chain]
addr = sorted({r['pool'].lower() for r in rows if len(r['pool']) == 42})
ids = sorted({r['pool'].lower() for r in rows if len(r['pool']) == 66})
def rpc(m, p):
    for k in range(40):
        try:
            rq = urllib.request.Request(url, json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': m, 'params': p}).encode(), {'Content-Type': 'application/json', 'User-Agent': 'curl/8'})
            with urllib.request.urlopen(rq, timeout=120) as f: j = json.loads(f.read())
            return j
        except urllib.error.HTTPError as e: time.sleep(5 + k if e.code in (429, 502, 503, 504) else 15)
        except Exception: time.sleep(5)
    raise SystemExit('rpc: no response')
def bts(n):
    j = rpc('eth_getBlockByNumber', [hex(n), False])
    return int(j['result']['timestamp'], 16)
def find(target, lo, hi):
    while lo < hi:
        m = (lo + hi) // 2
        if bts(m) < target: lo = m + 1
        else: hi = m
    return lo
d = os.path.join(out, chain); os.makedirs(d, exist_ok=True)
cur = os.path.join(d, 'cursor')
if os.path.exists(cur): b, end = map(int, open(cur).read().split())
else:
    head = int(rpc('eth_blockNumber', [])['result'], 16); now = bts(head); s = (now - bts(head - 1000000)) / 1e6
    est = lambda t: max(1, head - int((now - t) / s))
    b = find(T0, max(1, est(T0) - 2_000_000), min(head, est(T0) + 2_000_000))
    end = find(T1 + 1, b, min(head, est(T1 + 1) + 2_000_000)) - 1
    open(cur, 'w').write(f'{b} {end}')
print(chain, len(addr), 'addr', len(ids), 'v4 ids; blocks', b, end, flush=True)
tsc = {}
def tsof(n):
    if n not in tsc:
        if len(tsc) > 200000: tsc.clear()
        tsc[n] = bts(n)
    return tsc[n]
fn = os.path.join(d, 'logs.jsonl'); nrows = 0
def query(lo, hi):
    """возвращает логи [lo,hi] или None при превышении лимита"""
    res = []
    flt = []
    if addr: flt.append({'fromBlock': hex(lo), 'toBlock': hex(hi), 'address': addr, 'topics': [SW]})
    if ids: flt.append({'fromBlock': hex(lo), 'toBlock': hex(hi), 'topics': [SW4, ids]})
    for f in flt:
        if 'address' not in f and hi - lo + 1 > 30000: return None
        j = rpc('eth_getLogs', [f])
        if 'error' in j:
            if hi > lo: return None
            raise SystemExit('rpc error ' + json.dumps(j['error']))
        res += j['result']
    return res
step = 30000
with open(fn, 'a', encoding='utf-8') as w:
    while b <= end:
        hi = min(end, b + step - 1)
        r = query(b, hi)
        if r is None:
            if hi == b: raise SystemExit('single block fails')
            step = max(1, step // 2); continue
        need = sorted({int(lg['blockNumber'], 16) for lg in r} - tsc.keys())
        for i in range(0, len(need), 100):   # батч-запросы времени блоков
            part = need[i:i + 100]
            for k in range(40):
                try:
                    rq = urllib.request.Request(url, json.dumps([{'jsonrpc': '2.0', 'id': n, 'method': 'eth_getBlockByNumber', 'params': [hex(n), False]} for n in part]).encode(), {'Content-Type': 'application/json', 'User-Agent': 'curl/8'})
                    with urllib.request.urlopen(rq, timeout=120) as f: jj = json.loads(f.read())
                    for x in jj: tsc[x['id']] = int(x['result']['timestamp'], 16)
                    if all(n in tsc for n in part): break
                except Exception: time.sleep(5)
        for lg in r:
            n = int(lg['blockNumber'], 16)
            w.write(json.dumps({'b': n, 't': tsof(n), 'a': lg['address'], 'tp': lg['topics'], 'd': lg['data'], 'tx': lg['transactionHash'], 'li': lg['logIndex']}) + '\n'); nrows += 1
        w.flush(); b = hi + 1; open(cur, 'w').write(f'{b} {end}')
        if len(r) < 3000: step = min(30000, step * 2)
        print(chain, 'block', b, 'rows', nrows, flush=True)
json.dump({'chain': chain, 'rows': nrows, 'addr': len(addr), 'ids': len(ids)}, open(os.path.join(d, 'DONE.json'), 'w'))
