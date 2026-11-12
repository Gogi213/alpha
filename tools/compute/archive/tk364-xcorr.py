#!/usr/bin/env python3
"""TK-364 шаг 2: кросс-корреляция доходностей DEX (главный пул) и Bybit perp, сетка BIN сек, лаги ±LAGS бинов.
usage: tk364-xcorr.py COIN SYMBOL DAYS(15,16,17) NET BIN LAGS
k>0: corr(r_dex[t], r_bybit[t+k]) -> DEX опережает."""
import sys, os, csv, gzip, math, urllib.request, collections
coin, sym, days, net, BIN, LAGS = sys.argv[1], sys.argv[2], sys.argv[3].split(','), sys.argv[4], int(sys.argv[5]), int(sys.argv[6])
root = '/data/tk0364/usd'
D0 = 1792022400  # 2026-10-15 00:00 UTC
t0 = D0 + (int(days[0]) - 15) * 86400
t1 = D0 + (int(days[-1]) - 15 + 1) * 86400
n = (t1 - t0) // BIN
# DEX: главный пул = больше свопов
cnt = collections.Counter(); rows = []
for d in days:
    p = f'{root}/{net}/{coin}/2026-10-{d}.csv'
    if not os.path.exists(p): continue
    for r in csv.DictReader(open(p)):
        if not r['price_usd']: continue
        cnt[r['pool']] += 1; rows.append((int(r['ts']), int(r['logidx']), r['pool'], float(r['price_usd'])))
pool = cnt.most_common(1)[0][0]
rows = sorted(x for x in rows if x[2] == pool)
dex = [None] * n
for ts, _, _, px in rows:
    i = (ts - t0) // BIN
    if 0 <= i < n: dex[i] = px
# Bybit trades
by = [None] * n
for d in days:
    f = f'/tmp/tk364x/{sym}{"2026-10-"+d}.csv.gz'
    os.makedirs('/tmp/tk364x', exist_ok=True)
    if not os.path.exists(f):
        urllib.request.urlretrieve(f'https://public.bybit.com/trading/{sym}/{sym}2026-10-{d}.csv.gz', f)
    with gzip.open(f, 'rt') as fh:
        h = fh.readline().strip().split(','); ti, pi = h.index('timestamp'), h.index('price')
        for line in fh:
            a = line.split(','); i = int((float(a[ti]) - t0) // BIN)
            if 0 <= i < n: by[i] = float(a[pi])
def ff(x):
    last = None
    for i, v in enumerate(x):
        if v is None: x[i] = last
        else: last = v
    return x
dex, by = ff(dex), ff(by)
def rets(x):
    return [(math.log(x[i] / x[i-1]) if x[i] and x[i-1] else None) for i in range(1, len(x))]
rd, rb = rets(dex), rets(by)
def corr(k):
    xs = []; ys = []
    for i in range(len(rd)):
        j = i + k
        if 0 <= j < len(rb) and rd[i] is not None and rb[j] is not None: xs.append(rd[i]); ys.append(rb[j])
    m = len(xs)
    if m < 100: return None, m
    mx = sum(xs) / m; my = sum(ys) / m
    sxy = sum((a - mx) * (b - my) for a, b in zip(xs, ys)); sxx = sum((a - mx) ** 2 for a in xs); syy = sum((b - my) ** 2 for b in ys)
    return (sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else None), m
nd = sum(1 for v in rd if v)
print(f'{coin} {sym} {net} pool={pool} swaps={len(rows)} bin={BIN}s n={n} dex_nonzero_ret={sum(1 for v in rd if v)} ')
res = [(k, *corr(k)) for k in range(-LAGS, LAGS + 1)]
for k, c, m in res:
    if c is not None and (abs(k) <= 5 or k % 5 == 0 or c == max(r[1] or -9 for r in res)): print(f'lag {k*BIN:+5d}s corr {c:+.4f} n={m}')
best = max((r for r in res if r[1] is not None), key=lambda r: r[1])
print('BEST lag', best[0] * BIN, 's corr', round(best[1], 4), ' se~', round(1 / math.sqrt(best[2]), 4))
