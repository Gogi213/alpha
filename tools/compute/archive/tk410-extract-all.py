#!/usr/bin/env python3
"""TK-410 П-31 W8: пакетное извлечение середины+ликвидности (v3 Swap sqrtPriceX96/liquidity, v2 Sync резервы)
по всем пулам missing-pools.csv за один проход по /data/tk0393/sqd10/<chain>/part-*.jsonl. Формат как tk410-extract.py.
Запуск (calc): tk410-extract-all.py MISSING.csv OUTDIR   -> OUTDIR/<chain>__<pool>.csv + OUTDIR/summary.csv"""
import sys, glob, json, csv, os
from collections import defaultdict
miss, outd = sys.argv[1:3]; os.makedirs(outd, exist_ok=True)
T3 = ('0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67', '0x19b47279256b2a23a1665c810c8d55a1758940ee09377d4f8d26497a3577dc83')
T2 = '0x1c411e9a96e071241c2f21f7726b17ae89e3cab4c78be50e062b03a9fffbbad1'
def s256(h):
    v = int(h, 16); return v - (1 << 256) if v >> 255 else v
pools = defaultdict(dict)  # chain -> {pool_lower: coin}
for r in csv.DictReader(open(miss)): pools[r['chain']][r['pool'].lower()] = r['coin']
summ = []
for chain, pm in pools.items():
    fh = {}; cnt = defaultdict(int)
    for pl in pm:
        f = open(f'{outd}/{chain}__{pl}.csv', 'w', newline=''); w = csv.writer(f)
        w.writerow('ts,block,logidx,kind,mid_raw,liq0,liq1,tick'.split(',')); fh[pl] = (f, w)
    for p in sorted(glob.glob(f'/data/tk0393/sqd10/{chain}/part-*.jsonl')):
        for line in open(p):
            r = json.loads(line); hd = r['header']
            for lg in r.get('logs', ()):
                a = lg['address'].lower()
                if a not in fh: continue
                t0 = lg['topics'][0]; d = lg['data'][2:]; ws = [d[i:i + 64] for i in range(0, len(d), 64)]
                w = fh[a][1]
                if t0 in T3 and len(ws) >= 5:
                    w.writerow([hd['timestamp'], hd['number'], lg['logIndex'], 'v3', repr((int(ws[2], 16) / 2 ** 96) ** 2), int(ws[3], 16), '', s256(ws[4])]); cnt[a] += 1
                elif t0 == T2 and len(ws) >= 2:
                    r0 = int(ws[0], 16); r1 = int(ws[1], 16)
                    if r0 > 0: w.writerow([hd['timestamp'], hd['number'], lg['logIndex'], 'v2', repr(r1 / r0), r0, r1, '']); cnt[a] += 1
    for pl, (f, _) in fh.items(): f.close(); summ.append((chain, pm[pl], pl, cnt[pl]))
    print(chain, len(pm), sum(cnt.values()), flush=True)
with open(f'{outd}/summary.csv', 'w', newline='') as f:
    w = csv.writer(f); w.writerow('chain,coin,pool,n'.split(',')); w.writerows(summ)
print('pools_with_data', sum(1 for s in summ if s[3] > 0), 'of', len(summ))
