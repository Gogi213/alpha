#!/usr/bin/env python3
"""TK-392: середина пула v3 по sqrtPriceX96 из сырых логов SQD. Запуск: tk392-mid.py CHAIN POOL TOKEN QUOTE OUT.csv.gz
Строки: ts,block,logidx,mid (mid = цена токена в квоте в сырых единицах: (sqrtP/2^96)^2 или обратная; decimals не нужны — события и отклик относительные).
Swap v3: data = amount0,amount1,sqrtPriceX96,liquidity,tick (слова по 32 байта; PancakeV3 — ещё 2 слова комиссий)."""
import sys, json, gzip, subprocess, glob
chain, pool, token, quote, out = sys.argv[1:6]
pool = pool.lower(); t0 = token.lower() < quote.lower()
rows = []
p = subprocess.Popen(['grep', '-h', '-F', '-i', pool] + sorted(glob.glob(f'/data/tk0380/sqd/{chain}/part-*.jsonl')), stdout=subprocess.PIPE, text=True)
for line in p.stdout:
    b = json.loads(line); h = b['header']
    for lg in b.get('logs', []):
        if lg['address'].lower() != pool: continue
        d = lg['data'][2:]
        if len(d) < 5 * 64: continue
        sp = int(d[128:192], 16)
        if sp == 0: continue
        px = (sp / 2 ** 96) ** 2
        rows.append((h['timestamp'], h['number'], lg['logIndex'], px if t0 else 1 / px))
rows.sort()
with gzip.open(out, 'wt') as f:
    for r in rows: f.write('%d,%d,%d,%.12g\n' % r)
print(len(rows), 'swaps', out)
