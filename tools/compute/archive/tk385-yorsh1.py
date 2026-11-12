#!/usr/bin/env python3
"""TK-385 шаг 1 (псевдотест «ёршик/пила»): колебания середины стакана Gate фьюч (BBO, TK-380) вокруг линии DEX (Solana, swaps-usd TK-380).
Сетка 10 с, DEX ffill<=60 с (выбросы +-10% от медианы +-30 соседей, своп>=5$). Размах: полный ход от d<=-k до d>=+k или обратно, d=(mid-dex)/dex, k=0,2/0,5/1 %.
Запуск: tk385-yorsh1.py OUT.csv  (calc: /data/tk0380/solout/swaps-usd.csv, /data/tk0380/bbo/<COIN>.csv.gz)"""
import sys, os, csv, gzip, statistics, datetime, collections
OUT = sys.argv[1]; B = 10
D0 = int(datetime.datetime(2026, 10, 15, tzinfo=datetime.timezone.utc).timestamp()); ND = 8; N = ND * 86400 // B
KS = (0.002, 0.005, 0.01)
SW = collections.defaultdict(list)
for r in csv.DictReader(open('/data/tk0380/solout/swaps-usd.csv')):
    try: px = float(r['price_usd']); usd = float(r['usd'])
    except ValueError: continue
    if usd < 5 or px <= 0: continue
    SW[r['coin']].append((int(r['ts']), int(r['slot']), int(r['txidx']), px, usd, r['quote']))
def grid(pts):
    a = [float('nan')] * N
    for t, p in pts:
        i = int(t - D0) // B
        if 0 <= i < N: a[i] = p
    return a
def ffill(a, lim):
    o = a[:]; l = None; li = -10 ** 9
    for i in range(N):
        if a[i] == a[i]: l = a[i]; li = i
        elif l is not None and i - li <= lim: o[i] = l
    return o
def clean(rows, band=0.1, win=30):
    rows = sorted(rows); ps = [r[3] for r in rows]; out = []
    for k, r in enumerate(rows):
        w = sorted(ps[max(0, k - win):k + win + 1]); m = w[len(w) // 2]
        if abs(r[3] / m - 1) <= band: out.append(r)
    return out
def loadbbo(c):
    p = f'/data/tk0380/bbo/{c}.csv.gz'
    if not os.path.exists(p): return None
    bid = [float('nan')] * N; ask = [float('nan')] * N
    for line in gzip.open(p, 'rt'):
        t, b, k = line.rstrip().split(',')
        i = (int(t) - D0) // B
        if 0 <= i < N and b and k: bid[i] = float(b); ask[i] = float(k)
    return ffill(bid, 6), ffill(ask, 6)
def swings(d, k):
    """число полных ходов -k<->+k; по суткам"""
    st = 0; n = [0] * ND
    for i, x in enumerate(d):
        if x != x: continue
        s = -1 if x <= -k else (1 if x >= k else 0)
        if s == 0: continue
        if st != 0 and s != st: n[i * B // 86400] += 1
        st = s
    return n
rows = []
for c in sorted(SW):
    bb = loadbbo(c)
    if bb is None: continue
    q = collections.Counter()
    for r in SW[c]: q[r[5]] += r[4]
    qm = q.most_common(1)[0][0]
    sw = clean([r for r in SW[c] if r[5] == qm])
    if len(sw) < 200: continue
    dg = ffill(grid([(r[0], r[3]) for r in sw]), 6)
    bid, ask = bb
    d = []; spr = []
    for i in range(N):
        if dg[i] == dg[i] and bid[i] == bid[i] and ask[i] == ask[i] and ask[i] >= bid[i]:
            m = (bid[i] + ask[i]) / 2; d.append(m / dg[i] - 1); spr.append((ask[i] - bid[i]) / m * 1e4)
        else: d.append(float('nan'))
    nb = len(spr)
    if nb < 2000: continue
    hrs = nb * B / 3600
    ok = [x for x in d if x == x]
    row = dict(coin=c, quote=qm, swaps=len(sw), usd=round(sum(r[4] for r in sw)), hours=round(hrs, 1),
               spread_med_bps=round(statistics.median(spr), 1), dev_med_bps=round(statistics.median(ok) * 1e4, 1),
               dev_mad_bps=round(statistics.median(abs(x - statistics.median(ok)) for x in ok) * 1e4, 1))
    for k in KS:
        n = swings(d, k)
        row[f'sw_h_{k*100:g}'] = round(sum(n) / hrs, 3)
        row[f'sw_days_{k*100:g}'] = '/'.join(map(str, n))
        row[f'share_out_{k*100:g}'] = round(sum(1 for x in ok if abs(x) >= k) / len(ok), 3)
    rows.append(row)
with open(OUT, 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
print(len(rows), 'coins')
