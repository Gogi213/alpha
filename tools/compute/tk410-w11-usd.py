#!/usr/bin/env python3
"""TK-410 W11/ориентация: сырьё пула (ts,block,logidx,kind,mid_raw,...) -> середина монеты в USD для tk410-map2.
Ориентация s=±1 (мид пула = token1/token0): максимум корреляции часовых приращений ln(s*ln p + ln Q) с ln gev2 монеты; Q — gev2 котировки
(BNB/ETH/PAXG; '-' = стейбл, Q=1). Своп без котировки старше 60 с отбрасывается, доля — в лог.
Запуск (calc): tk410-w11-usd.py COIN RAWCSV QUOTE OUTCSV"""
import sys, os, glob, numpy as np
coin, rawf, quote, outf = sys.argv[1:5]
GEV = '/data/tk0393/tk401/gev2all'
def load(c):
    ps = [np.load(f) for f in sorted(glob.glob(f'{GEV}/{c}/??????????.npy')) if int(open(f[:-4] + '.n').read() or -1) >= 100]
    a = np.concatenate(ps); a = a[np.argsort(a[:, 0], kind='stable')]
    ok = np.isfinite(a[:, 1]) & np.isfinite(a[:, 2]) & (a[:, 1] < a[:, 2]); a = a[ok]
    return a[:, 0].astype(np.int64), np.log((a[:, 1] + a[:, 2]) / 2)
raw = np.genfromtxt(rawf, delimiter=',', names=True, dtype=None, encoding=None)
_, ui = np.unique(np.stack([raw['block'], raw['logidx']], 1), axis=0, return_index=True)
raw = raw[np.sort(ui)]; raw = raw[np.argsort(raw['ts'], kind='stable')]
ts = raw['ts'].astype(np.int64) * 1000; lp = np.log(raw['mid_raw'].astype(np.float64))
gt, gm = load(coin)
def at(tt, tarr, varr, age=60000):
    k = np.searchsorted(tarr, tt, side='right') - 1; kc = np.clip(k, 0, None)
    return varr[kc], (k >= 0) & (tt - tarr[kc] <= age)
if quote == '-': lq = np.zeros(len(ts)); okq = np.ones(len(ts), bool)
else:
    qt, qm = load(quote); lq, okq = at(ts, qt, qm)
H = 3600000; hs = np.arange(ts.min() // H, ts.max() // H + 1) * H
best = None
for s in (1, -1):
    pu = s * lp + lq
    a, oa = at(hs, ts, pu, 3 * H); b, ob = at(hs, gt, gm, 3 * H)
    m = oa & ob & np.concatenate([[False], (oa & ob)[1:] & (oa & ob)[:-1]])
    da, db = np.diff(a)[m[1:]], np.diff(b)[m[1:]]
    c = float(np.corrcoef(da, db)[0, 1]) if len(da) > 10 and da.std() > 0 and db.std() > 0 else float('nan')
    print(coin, 's', s, 'corr', round(c, 3), 'hours', len(da), flush=True)
    if best is None or (c == c and (best[1] != best[1] or c > best[1])): best = (s, c)
s = best[0]; pu = s * lp + lq
print(coin, 'orient', s, 'corr', best[1], 'rows', len(ts), 'dropped_noquote', int((~okq).sum()), flush=True)
keep = okq
with open(outf, 'w') as f:
    f.write('ts,block,logidx,kind,mid_raw,liq0,liq1,tick\n')
    for i in np.nonzero(keep)[0]:
        f.write(f"{raw['ts'][i]},{raw['block'][i]},{raw['logidx'][i]},{raw['kind'][i]},{np.exp(pu[i])!r},,,\n")
