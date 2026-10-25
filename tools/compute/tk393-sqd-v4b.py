#!/usr/bin/env python3
"""TK-393: свопы Uniswap v4 (Base, PoolManager 0x4985…2b) через SQD Portal: PoolManager + topic0 Swap v4 + topic1 = poolId.
Использование: tk380-sqd-v4.py <pools.csv (coin,chain,pool)> <cursor (b end)> <outdir>
Выход: <outdir>/swaps.csv.gz (amount0/amount1 с точки зрения пула, как в tk380-sqd-parse: + в пул, − из пула), summary.csv, DONE.json."""
import csv, glob, gzip, json, os, sys, time, collections, urllib.request, urllib.error
pools_csv, cur, out = sys.argv[1:4]
B = 'https://portal.sqd.dev/datasets/base-mainnet'
PM = '0x498581ff718922c3f8e6a244956af099b2652b2b'
SW = '0x40e9cecb9f5f1f1c5b9c97dec2917b7ee92e57ba5563708daca94dd84ad7112f'
ids = sorted({r['pool'].lower() for r in csv.DictReader(open(pools_csv, encoding='utf-8')) if r['chain'] == 'base' and len(r['pool']) == 66})
b, end = map(int, open(cur).read().split())
os.makedirs(out, exist_ok=True)
def post(q):
    for k in range(60):
        try:
            r = urllib.request.Request(B + '/stream', data=json.dumps(q).encode(), headers={'Content-Type': 'application/json', 'User-Agent': 'curl/8'})
            with urllib.request.urlopen(r, timeout=300) as f: return f.read().decode()
        except urllib.error.HTTPError as e:
            if e.code in (429, 503, 504, 529): time.sleep(10); continue
            if e.code == 204: return ''
            raise
        except Exception: time.sleep(10)
    raise SystemExit('sqd: no response')
def s256(h):
    v = int(h, 16); return v - (1 << 256) if v >> 255 else v
print(len(ids), 'poolIds blocks', b, end, flush=True)
summ = collections.defaultdict(lambda: [0, set()]); n = 0
# докачка: каждый запуск пишет swaps-<стартовый блок>.csv.gz, курсор «b end» обновляется после пачки; сводка — по всем частям
with gzip.open(os.path.join(out, f'swaps-{b}.csv.gz'), 'wt', newline='') as g:
    w = csv.writer(g); w.writerow(['ts', 'block', 'txidx', 'logidx', 'pool', 'kind', 'amount0', 'amount1', 'sender', 'gas_price', 'gas_used', 'txhash'])
    while b <= end:
        q = {"type": "evm", "fromBlock": b, "toBlock": end,
             "fields": {"block": {"number": True, "timestamp": True},
                        "log": {"address": True, "topics": True, "data": True, "transactionHash": True, "transactionIndex": True, "logIndex": True},
                        "transaction": {"from": True, "gasPrice": True, "gasUsed": True, "transactionIndex": True}},
             "logs": [{"address": [PM], "topic0": [SW], "topic1": ids, "transaction": True}]}
        s = post(q)
        if not s.strip(): break
        for line in s.splitlines():
            r = json.loads(line); h = r['header']; tx = {t['transactionIndex']: t for t in r.get('transactions', [])}
            for l in r.get('logs', []):
                d = l['data'][2:]; a0, a1 = -s256(d[:64]), -s256(d[64:128])  # v4: дельты свопера -> знак пула
                t = tx.get(l['transactionIndex'], {}); pid = l['topics'][1].lower()
                w.writerow([h['timestamp'], h['number'], l['transactionIndex'], l['logIndex'], pid, 'v4', a0, a1, t.get('from', ''),
                            int(t['gasPrice'], 16) if t.get('gasPrice') else '', int(t['gasUsed'], 16) if t.get('gasUsed') else '', l['transactionHash']])
                e = summ[(pid, h['timestamp'] // 86400)]; e[0] += 1; e[1].add(t.get('from', '')); n += 1
        b = json.loads(s.splitlines()[-1])['header']['number'] + 1
        print('block', b, 'swaps', n, flush=True); g.flush(); open(cur, 'w').write(f'{b} {end}')
summ = collections.defaultdict(lambda: [0, set()]); n = 0
for fn in sorted(glob.glob(os.path.join(out, 'swaps*.csv.gz'))):
    try:
        for r in csv.DictReader(gzip.open(fn, 'rt', newline='')):
            e = summ[(r['pool'], int(r['ts']) // 86400)]; e[0] += 1; e[1].add(r['sender']); n += 1
    except EOFError: pass  # оборванный oom-снятием хвост gzip
with open(os.path.join(out, 'summary.csv'), 'w', newline='') as f:
    w = csv.writer(f); w.writerow(['pool', 'day_utc', 'swaps', 'senders'])
    for (p, d), (c, u) in sorted(summ.items()): w.writerow([p, d, c, len(u)])
json.dump({'done': True, 'swaps': n, 'pools': len({p for p, _ in summ}), 'pools_asked': len(ids)}, open(os.path.join(out, 'DONE.json'), 'w'))
