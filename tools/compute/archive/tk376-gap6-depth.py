#!/usr/bin/env python3
"""TK-376 проход 6, п.3: глубина v3 для эпизодов open_gap >= 50 бп (все, не выборка). Из raw Swap (tx,logidx -> data: amount0, amount1, sqrtPriceX96, liquidity, tick)
берётся liquidity и sqrtPrice после открывающего свопа (tx из episodes.csv, последний logidx этого пула в tx). Виртуальный резерв квотируемой стороны
V_usd = (L*sqrtP/10^d1 | L/sqrtP/10^d0) * quote_usd; проскальзывание входа N=1000 USD в пределах тика ~ N/V (бп, среднее по ходу), ход цены 2N/V; flag cross = ход цены > 2*fee_bp (шаг тика ~ 2*fee бп).
Запуск: depth.py episodes.csv out.csv"""
import sys, csv, json, os, math, collections
EP, OUT = sys.argv[1], sys.argv[2]
FEES = json.load(open('/data/tk0376/pool-fees.json')); META = json.load(open('/data/tk0364/usd/pool-meta.json'))
RAW = ['/data/tk0364/outw', '/data/tk0364/outw2', '/data/tk0364/outw3', '/data/tk0364/outw4', '/data/tk0364/outw-wrap']
def w(d):
    d = d[2:]; return [d[i:i + 64] for i in range(0, len(d), 64)]
need = collections.defaultdict(dict)   # (net,coin,day) -> {tx: idx}
rows = []
for r in csv.DictReader(open(EP)):
    if abs(float(r['open_gap'])) < 50: continue
    fe = FEES.get(f"{r['net']}:{r['pool']}") or {}
    if not fe.get('src', '').startswith('v3'): continue
    rows.append(r); need[(r['net'], r['coin'], r['day'])][r['tx']] = 1
print('episodes v3 >=50:', len(rows), 'coin-days', len(need), flush=True)
liq = {}   # (net,coin,day,tx) -> (sqrtP, L, quote_usd, price_quote, pool)
miss = 0
for (net, coin, day), txs in need.items():
    uf = f'/data/tk0364/usd/{net}/{coin}/2026-10-{int(day):02d}.csv'
    if not os.path.exists(uf): continue
    urow = {}
    for u in csv.DictReader(open(uf)):
        if u['tx'] in txs: urow[(u['tx'], int(u['logidx']))] = u
    if not urow: continue
    for rd in RAW:
        for chain in (net, 'bsc-core') if net == 'bsc' else (net,):
            f = f'{rd}/{chain}/{coin}/2026-10-{int(day):02d}.csv'
            if not os.path.exists(f): continue
            for x in csv.DictReader(open(f)):
                k = (x['tx'], int(x['logidx']))
                if k in urow and x['kind'] in ('v3', 'pcs-v3'):
                    ww = w(x['data'])
                    try: liq[(net, coin, day, x['tx'], urow[k]['pool'].lower())] = (int(ww[2], 16) / 2 ** 96, int(ww[3], 16), float(urow[k]['quote_usd'] or 0), float(urow[k]['price_quote']), x['logidx'])
                    except Exception: pass
with open(OUT, 'w', newline='') as fo:
    wr = csv.writer(fo); wr.writerow(['net', 'coin', 'pool', 'day', 'open_gap', 'dur_s', 'fee_bp', 'net_1', 'swap_usd', 'V_usd', 'slip_bp', 'move_bp', 'cross', 'dex'])
    for r in rows:
        k = (r['net'], r['coin'], r['day'], r['tx'], r['pool'].lower())
        m = META.get(f"{r['net']}:{r['pool'].lower()}")
        if k not in liq or not m: miss += 1; continue
        sq, L, qusd, pq, _ = liq[k]; d0, d1 = m['dec']
        p = sq * sq * 10 ** (d0 - d1)
        if p <= 0 or pq <= 0 or L == 0 or qusd <= 0: miss += 1; continue
        base_is0 = abs(math.log(pq / p)) < abs(math.log(pq * p))
        V = (L * sq / 10 ** d1 if base_is0 else L / sq / 10 ** d0) * qusd
        if V <= 0: miss += 1; continue
        slip = 1000.0 / V * 1e4; move = 2 * slip; fe = float(r['fee_bp'])
        wr.writerow([r['net'], r['coin'], r['pool'], r['day'], r['open_gap'], r['dur_s'], fe, r['net_1'], r['swap_usd'], round(V, 1), round(slip, 3), round(move, 3), int(move > 2 * fe), r['dex']])
print('missing', miss, 'of', len(rows))
