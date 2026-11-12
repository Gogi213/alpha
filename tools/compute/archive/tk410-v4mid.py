#!/usr/bin/env python3
"""TK-410 П-31 W8: середина пула Uniswap v4 (sqrtPriceX96/liquidity/tick из data Swap) через SQD Portal.
Использование: tk410-v4mid.py <cover.csv> <chain> <dataset> <PoolManager> <fromBlock> <toBlock> <outdir>
Выход: <outdir>/<chain>__<poolId>.csv (формат tk410-extract-all: ts,block,logidx,kind,mid_raw,liq0,liq1,tick), summary.csv.
data Swap v4: amount0,amount1,sqrtPriceX96,liquidity,tick,fee (слова по 32 байта)."""
import csv, json, os, sys, time, urllib.request, urllib.error
cov, chain, ds, pm, b, end, out = sys.argv[1:8]; b, end = int(b), int(end)
B = f'https://portal.sqd.dev/datasets/{ds}'
SW = '0x40e9cecb9f5f1f1c5b9c97dec2917b7ee92e57ba5563708daca94dd84ad7112f'
ids = sorted({r['pool'].lower() for r in csv.DictReader(open(cov, encoding='utf-8')) if r['chain'] == chain and len(r['pool']) == 66})
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
fh = {}; cnt = {i: 0 for i in ids}
for i in ids:
    f = open(f'{out}/{chain}__{i}.csv', 'w', newline=''); w = csv.writer(f)
    w.writerow('ts,block,logidx,kind,mid_raw,liq0,liq1,tick'.split(',')); fh[i] = (f, w)
print(chain, len(ids), 'poolIds blocks', b, end, flush=True)
while b <= end:
    q = {"type": "evm", "fromBlock": b, "toBlock": end,
         "fields": {"block": {"number": True, "timestamp": True}, "log": {"address": True, "topics": True, "data": True, "logIndex": True}},
         "logs": [{"address": [pm], "topic0": [SW], "topic1": ids}]}
    s = post(q)
    if not s.strip(): break
    for line in s.splitlines():
        r = json.loads(line); hd = r['header']
        for lg in r.get('logs', ()):
            d = lg['data'][2:]; ws = [d[i:i + 64] for i in range(0, len(d), 64)]
            pid = lg['topics'][1].lower()
            if pid in fh and len(ws) >= 5:
                fh[pid][1].writerow([hd['timestamp'], hd['number'], lg['logIndex'], 'v4', repr((int(ws[2], 16) / 2 ** 96) ** 2), int(ws[3], 16), '', s256(ws[4])]); cnt[pid] += 1
    b = json.loads(s.splitlines()[-1])['header']['number'] + 1
    print('block', b, flush=True)
for f, _ in fh.values(): f.close()
with open(f'{out}/summary-{chain}.csv', 'w', newline='') as f:
    w = csv.writer(f); w.writerow('chain,pool,n'.split(',')); w.writerows((chain, i, cnt[i]) for i in ids)
print('pools_with_data', sum(1 for i in ids if cnt[i] > 0), 'of', len(ids))
