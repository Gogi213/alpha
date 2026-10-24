#!/usr/bin/env python3
"""TK-385 шаг 2 (модель лимиток; база — код шага 1). TK-385 шаг 1 (псевдотест «ёршик/пила»): колебания середины стакана Gate фьюч (BBO, TK-380) вокруг линии DEX (Solana, swaps-usd TK-380).
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

def trades(c):
    d = f'/srv/alpha-sda/tk372-gate/raw/fut/{c}_USDT'; out = []
    if not os.path.isdir(d): return out
    for fn in sorted(os.listdir(d)):
        for r in csv.DictReader(gzip.open(f'{d}/{fn}', 'rt')):
            t = float(r['create_time_ms'] or r['create_time']); sz = float(r['size'])
            if D0 <= t < D0 + ND * 86400: out.append((t, 1 if sz > 0 else -1, float(r['price'])))
    out.sort(); return out
MK, TK = -1.0, 7.5  # бп: ребейт мейкера (отриц. комиссия), тейкер Gate фьюч
Ts = (300, 1800)
def sim(tr, dg, bid, ask, k, T, strict):
    """N=1 лот. Покупка DEX*(1-k) исполняется сделкой-продажей (sg=-1) по цене <(строго) или <= цены; продажа DEX*(1+k) — сделкой-покупкой.
    Котировка по DEX/BBO предыдущей секунды (задержка 1 с). Вход только если котировка внутри стакана (не кроссует). Выход: встречная котировка либо рынок через T."""
    pos = 0; ent = 0.0; te = 0; n_rt = 0; n_forced = 0; pnl = 0.0; nin = 0; rts = []
    def force(t_end):
        nonlocal pos, pnl, n_rt, n_forced
        j = min(t_end, ND * 86400 - 1); b, a = bid[j], ask[j]
        if pos == 1 and b == b: x = (b / ent - 1) * 1e4 + (-MK) - TK
        elif pos == -1 and a == a: x = (ent / a - 1) * 1e4 + (-MK) - TK
        else: x = None
        if x is not None: pnl += x; rts.append(x); n_rt += 1; n_forced += 1
        pos = 0
    for t, sg, p in tr:
        i = int(t - D0)
        if pos and i >= te: force(te)
        if i < 1: continue
        dx = dg[i - 1]; b = bid[i - 1]; a = ask[i - 1]
        if dx != dx or b != b or a != a: continue
        Pb = dx * (1 - k); Ps = dx * (1 + k)
        if pos == 0:
            if sg < 0 and (p < Pb if strict else p <= Pb) and Pb < a:
                pos = 1; ent = Pb; te = i + T; nin += 1
            elif sg > 0 and (p > Ps if strict else p >= Ps) and Ps > b:
                pos = -1; ent = Ps; te = i + T; nin += 1
        elif pos == 1 and sg > 0 and (p > Ps if strict else p >= Ps) and Ps > b and Ps > ent:
            x = (Ps / ent - 1) * 1e4 + 2 * (-MK); pnl += x; rts.append(x); n_rt += 1; pos = 0
        elif pos == -1 and sg < 0 and (p < Pb if strict else p <= Pb) and Pb < a and Pb < ent:
            x = (ent / Pb - 1) * 1e4 + 2 * (-MK); pnl += x; rts.append(x); n_rt += 1; pos = 0
    if pos: force(te)
    return nin, n_rt, n_forced, pnl, rts
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
    both = sum(1 for i in range(N) if dg[i] == dg[i] and bid[i] == bid[i])
    if both * B < 2000 * B: pass
    tr = trades(c)
    if len(tr) < 500: continue
    # сетка 10 с: индексы секунд -> блоки; пересобираем на 1 с через повтор
    dg1 = [dg[i // B] for i in range(ND * 86400)]; bid1 = [bid[i // B] for i in range(ND * 86400)]; ask1 = [ask[i // B] for i in range(ND * 86400)]
    hrs = sum(1 for i in range(0, ND * 86400, B) if dg[i // B] == dg[i // B] and bid[i // B] == bid[i // B]) * B / 3600
    if hrs < 5: continue
    for k in KS:
        for T in Ts:
            for strict in (True, False):
                nin, nrt, nf, pnl, rts = sim(tr, dg1, bid1, ask1, k, T, strict)
                rows.append(dict(coin=c, hours=round(hrs, 1), trades=len(tr), k_pct=k * 100, T_s=T, rule='through' if strict else 'touch',
                                 entries=nin, roundtrips=nrt, forced=nf, forced_share=round(nf / nrt, 3) if nrt else '',
                                 pnl_bp_sum=round(pnl, 1), pnl_bp_per_rt=round(pnl / nrt, 1) if nrt else '', pnl_bp_per_h=round(pnl / hrs, 2)))
    print(c, len(tr), round(hrs, 1), flush=True)
with open(OUT, 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
print(len(rows), 'rows')
