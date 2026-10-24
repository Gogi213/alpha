#!/usr/bin/env python3
"""TK-386: Sync-события v2-пулов (середина пула после свопа, спец. TK-384 п.3); копия tk380-sqd-evm.py с topic0=Sync. TK-380: свопы EVM-пулов через SQD Portal (без ключа). Использование: tk380-sqd-evm.py <dataset> <pools.csv> <chain> <outdir> <t0_utc> <t1_utc>
Пишет <outdir>/<chain>/blocks-<N>.jsonl (блок-заголовок, from/gasPrice/gasUsed транзакции, лог) и DONE.json. Докачка: <outdir>/<chain>/cursor."""
import csv, json, os, sys, time, urllib.request, urllib.error
ds, pools_csv, chain, out, t0, t1 = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5]), int(sys.argv[6])
B = f'https://portal.sqd.dev/datasets/{ds}'
SW = ['0x1c411e9a96e071241c2f21f7726b17ae89e3cab4c78be50e062b03a9fffbbad1']   # v2 Sync(reserve0,reserve1)
def post(path, q):
    for k in range(60):
        try:
            r = urllib.request.Request(B + path, data=json.dumps(q).encode(), headers={'Content-Type': 'application/json', 'User-Agent': 'curl/8'})
            with urllib.request.urlopen(r, timeout=120) as f:
                return f.read().decode()
        except urllib.error.HTTPError as e:
            if e.code in (429, 503, 504, 529): time.sleep(10); continue
            if e.code == 204: return ''
            raise
        except Exception: time.sleep(10)
    raise SystemExit('sqd: no response')
def ts(n):
    s = post('/stream', {"type": "evm", "fromBlock": n, "toBlock": n, "fields": {"block": {"number": True, "timestamp": True}}})
    return json.loads(s.splitlines()[0])['header']['timestamp'] if s.strip() else None
def find(target, lo, hi):
    while lo < hi:
        m = (lo + hi) // 2
        t = ts(m)
        if t is None: lo = m + 1; continue
        if t < target: lo = m + 1
        else: hi = m
    return lo
pools = sorted({r['pool'].lower() for r in csv.DictReader(open(pools_csv, encoding='utf-8')) if r['chain'] == chain and len(r['pool']) == 42})
d = os.path.join(out, chain); os.makedirs(d, exist_ok=True)
cur = os.path.join(d, 'cursor')
if os.path.exists(cur): b, end = map(int, open(cur).read().split())
else:
    head = json.loads(urllib.request.urlopen(urllib.request.Request(B + '/head', headers={'User-Agent': 'curl/8'}), timeout=60).read())['number']
    now = ts(head); sec = {'bsc': 0.46, 'ethereum': 12}.get(chain, 2)
    est = lambda t: max(1, head - int((now - t) / sec))
    b = find(t0, 1, head)
    end = find(t1 + 1, b, head) - 1
    open(cur, 'w').write(f'{b} {end}')
print(chain, len(pools), 'pools blocks', b, end, flush=True)
n = 0
while b <= end:
    q = {"type": "evm", "fromBlock": b, "toBlock": end,
         "fields": {"block": {"number": True, "timestamp": True},
                    "log": {"address": True, "topics": True, "data": True, "transactionHash": True, "transactionIndex": True, "logIndex": True},
                    },
         "logs": [{"address": pools, "topic0": SW}]}
    s = post('/stream', q)
    if not s.strip(): break
    open(os.path.join(d, f'part-{n:05d}.jsonl'), 'w').write(s)
    b = json.loads(s.splitlines()[-1])['header']['number'] + 1
    n += 1
    open(cur, 'w').write(f'{b} {end}')
    print(chain, 'block', b, flush=True)
json.dump({"done": True, "parts": n, "pools": len(pools)}, open(os.path.join(d, 'DONE.json'), 'w'))
