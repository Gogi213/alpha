#!/usr/bin/env python3
"""TK-410 Sui: середина пула из событий Swap (cetus pool::SwapEvent, momentum trade::SwapEvent, bluefin AssetSwap/FlashSwap) в формате tk410-extract.py.
Берутся события своего пула (pool|pool_id == пул) с before/after sqrt_price; mid_raw = (after_sqrt/2^64)^2; дубли (tx, after) снимаются.
Ориентация (b/a или a/b) — по знаку корреляции часовых приращений log(цена пула) с log(середина Gate фьюча gev2all) за период; при <0 цена обращается.
Запуск (calc): tk410-sui-mid.py COIN POOL OUTCSV   (ts в секундах UTC — время транзакции, как в jsonl TK-393)."""
import sys, json, glob, math, csv, os
import numpy as np
coin, pool, out = sys.argv[1:4]
GEV = os.environ.get('GEV', '/data/tk0393/tk401/gev2all')
rows = []; seen = set()
for ln, line in enumerate(open(f'/data/tk0393/sqd7/st/sui/{pool}.jsonl')):
    r = json.loads(line)
    for k, e in enumerate(r['ev']):
        j = e['json']; pid = j.get('pool') or j.get('pool_id')
        if pid != pool: continue
        s = j.get('after_sqrt_price') or j.get('sqrt_price_after')
        if s is None: continue
        key = (r['d'], s)
        if key in seen: continue
        seen.add(key); rows.append((r['t'], ln, k, (int(s) / 2 ** 64) ** 2))
rows.sort(key=lambda x: (x[0], x[1], x[2]))
ts = np.array([x[0] for x in rows]); lp = np.log(np.array([x[3] for x in rows]))
corr = float('nan'); sign = 1
parts = [np.load(f) for f in sorted(glob.glob(f'{GEV}/{coin}/??????????.npy'))]
if parts and len(ts) > 100:
    a = np.concatenate(parts); a = a[np.isfinite(a[:, 1]) & np.isfinite(a[:, 2]) & (a[:, 1] < a[:, 2])]
    gh = (a[:, 0] // 3600000).astype(np.int64); gm = np.log((a[:, 1] + a[:, 2]) / 2)
    ph = ts // 3600
    hs = np.intersect1d(np.unique(gh), np.unique(ph))
    gl = {h: gm[gh == h][-1] for h in hs}; pl = {h: lp[ph == h][-1] for h in hs}
    dg = np.diff([gl[h] for h in hs]); dp = np.diff([pl[h] for h in hs])
    if len(dg) > 30 and dg.std() > 0 and dp.std() > 0:
        corr = float(np.corrcoef(dg, dp)[0, 1]); sign = -1 if corr < 0 else 1
with open(out, 'w', newline='') as f:
    w = csv.writer(f); w.writerow('ts,block,logidx,kind,mid_raw,liq0,liq1,tick'.split(','))
    for (t, ln, k, p), l in zip(rows, lp): w.writerow([t, ln, k, 'v3', repr(math.exp(sign * l)), '', '', ''])
print(coin, pool[:10], 'rows', len(rows), 'corr_hourly', corr, 'sign', sign, flush=True)
