#!/usr/bin/env python3
"""TK-410 П-31 W8/W9: из сырых логов sqd (jsonl) одного пула — настоящая середина ПОСЛЕ свопа и ликвидность.
Запуск (calc): tk410-extract.py COIN CHAIN POOL OUTCSV   (сырьё /data/tk0393/sqd*/<chain>/part-*.jsonl)
v3 Swap (0xc42079f9... / pancake 0x19b47279...): data = amount0,amount1,sqrtPriceX96,liquidity,tick (слова по 32 байта) -> mid_raw=(sqrtP/2^96)^2, liq0=L.
v2 Sync (0x1c411e9a...): data = reserve0,reserve1 -> mid_raw=r1/r0, liq0,liq1=резервы.
Выход: ts,block,logidx,kind,mid_raw,liq0,liq1,tick; масштаб десятичных (10^k) подбирается на этапе карты по сверке с ценой исполнения."""
import sys, glob, json, csv
coin, chain, pool, out = sys.argv[1:5]; pool = pool.lower()
T3 = ('0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67', '0x19b47279256b2a23a1665c810c8d55a1758940ee09377d4f8d26497a3577dc83')
T2 = '0x1c411e9a96e071241c2f21f7726b17ae89e3cab4c78be50e062b03a9fffbbad1'
def s256(h):
    v = int(h, 16); return v - (1 << 256) if v >> 255 else v
n = 0; kinds = {}
with open(out, 'w', newline='') as fo:
    w = csv.writer(fo); w.writerow('ts,block,logidx,kind,mid_raw,liq0,liq1,tick'.split(','))
    import os
    for p in sorted(glob.glob(os.environ.get('SRC', f'/data/tk0393/sqd*/{chain}/part-*.jsonl'))):
        for line in open(p):
            if pool not in line: continue
            r = json.loads(line); hd = r['header']
            for lg in r['logs']:
                if lg['address'].lower() != pool: continue
                t0 = lg['topics'][0]; d = lg['data'][2:]; ws = [d[i:i + 64] for i in range(0, len(d), 64)]
                if t0 in T3 and len(ws) >= 5:
                    sp = int(ws[2], 16); liq = int(ws[3], 16); tk = s256(ws[4])
                    w.writerow([hd['timestamp'], hd['number'], lg['logIndex'], 'v3', repr((sp / 2 ** 96) ** 2), liq, '', tk]); n += 1; kinds['v3'] = kinds.get('v3', 0) + 1
                elif t0 == T2 and len(ws) >= 2:
                    r0 = int(ws[0], 16); r1 = int(ws[1], 16)
                    if r0 > 0:
                        w.writerow([hd['timestamp'], hd['number'], lg['logIndex'], 'v2', repr(r1 / r0), r0, r1, '']); n += 1; kinds['v2'] = kinds.get('v2', 0) + 1
print(coin, chain, pool, n, kinds)
