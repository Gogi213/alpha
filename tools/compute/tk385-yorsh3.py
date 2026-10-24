#!/usr/bin/env python3
"""TK-385 шаг 2 v2: «ёршик» с очередью по L2 Gate (TK-379: set = снимок раз в час, make/take = +/- объём на цене) и чистой серединой пула DEX (EVM, как tk386-converge).
Запуск: tk385-yorsh3.py COIN CHAIN MODE(raw|med) OUT.csv. Котировки DEX*(1∓k), округление к тику внутрь стакана; заявка стоит, пока округлённая цена не сменилась (смена = потеря очереди).
Исполнение: сделка Gate строго сквозь цену — заполнение; на цене — копим объём после постановки, заполнение когда накоплено > объёма впереди (стакан на момент постановки; отмены впереди не учтены).
queue=0 — без очереди (касание). N=1 лот. Выход: встречная котировка (цена хуже входа не берём) либо рынок через T. Издержки как в yorsh2: ребейт мейкера 1 бп, тейкер 7,5 бп."""
import sys, os, csv, gzip, json, glob, datetime
import numpy as np
COIN, CH, MODE, OUT = sys.argv[1:5]
B = 10; D0 = int(datetime.datetime(2026, 10, 15, tzinfo=datetime.timezone.utc).timestamp()); ND = 8; N = ND * 86400 // B; AGE = 6
EVM = {'SUE': dict(pool='0xfFCa411CFbcD3194503eFfA80Fd394b55387915c', q0=False, dt=18, dq=18, quote='BNB'),
       'LAPTOP': dict(pool='0x99cf3E8bfB02c300312c53Aac5D0B082e3D5975C', q0=True, dt=18, dq=6, quote=None, v3=True)}
def loadbbo(c):
    p = f'/data/tk0380/bbo/{c}.csv.gz'; bid = np.full(N, np.nan); ask = np.full(N, np.nan)
    for line in gzip.open(p, 'rt'):
        t, b, k = line.rstrip().split(',')
        i = (int(t) - D0) // B
        if 0 <= i < N and b and k: bid[i] = float(b); ask[i] = float(k)
    for a in (bid, ask):
        l = np.nan; li = -10 ** 9
        for i in range(N):
            if a[i] == a[i]: l = a[i]; li = i
            elif i - li <= 6: a[i] = l
    return bid, ask
def evm_mid(c, bnb=None):
    e = EVM[c]; pl = e['pool'].lower(); a = np.full(N, np.nan)
    for f in sorted(glob.glob(f'/data/tk0380/{"sqd" if e.get("v3") else "sqd-sync"}/{CH}/part-*.jsonl')):
        for line in open(f):
            if pl not in line: continue
            try: j = json.loads(line)
            except ValueError: continue
            ts = j['header']['timestamp']
            for lg in j.get('logs', []):
                if lg['address'].lower() != pl: continue
                d = lg['data'][2:]
                if e.get('v3'):
                    sq = int(d[128:192], 16)
                    if sq <= 0: continue
                    p01 = (sq / 2 ** 96) ** 2
                    px = (1 / p01 if e['q0'] else p01) * 10 ** (e['dt'] - e['dq']) if e['q0'] else p01 * 10 ** (e['dt'] - e['dq'])
                else:
                    r0 = int(d[:64], 16); r1 = int(d[64:128], 16)
                    if r0 <= 0 or r1 <= 0: continue
                    rq, rt = (r0, r1) if e['q0'] else (r1, r0)
                    px = (rq / 10 ** e['dq']) / (rt / 10 ** e['dt'])
                i = (ts - D0) // B
                if 0 <= i < N: a[i] = px
    o = a.copy(); l = np.nan; li = -10 ** 9
    for i in range(N):
        if a[i] == a[i]: l = a[i]; li = i
        elif i - li <= AGE: o[i] = l
    if e['quote'] == 'BNB': o = o * bnb
    return o
def rollbase(gap):
    base = np.full(N, np.nan); W = 8640
    for k in range(W, N, 360):
        w = gap[k - W:k]; w = w[w == w]
        if len(w) >= 200: base[k:k + 360] = np.median(w)
    return base
bd, ak = loadbbo(COIN); mid = np.where(ak > bd, (bd + ak) / 2, np.nan)
bq = None
if EVM[COIN]['quote'] == 'BNB': q = loadbbo('BNB'); bq = (q[0] + q[1]) / 2
dx = evm_mid(COIN, bq)
gap = np.where((mid == mid) & (dx == dx), np.log(mid / dx) * 1e4, np.nan)
if MODE == 'med': dx = dx * np.exp(rollbase(gap) / 1e4)   # до 16.10 NaN
dx1 = np.repeat(dx, B); bid1 = np.repeat(bd, B); ask1 = np.repeat(ak, B)
d = f'/srv/alpha-sda/tk372-gate/raw/fut/{COIN}_USDT'; ev = []   # (t, kind, sg, price, size): kind 0=book(+/-), 1=set, 2=trade
for fn in sorted(os.listdir(d)):
    for r in csv.DictReader(gzip.open(f'{d}/{fn}', 'rt')):
        t = float(r['create_time_ms'] or r['create_time']); sz = float(r['size'])
        if D0 <= t < D0 + ND * 86400: ev.append((t, 2, 1 if sz > 0 else -1, float(r['price']), abs(sz)))
trp = sorted({e[3] for e in ev[:20000]}); tick = min(round(b - a, 12) for a, b in zip(trp, trp[1:]) if b - a > 1e-12)
for fn in sorted(glob.glob(f'/srv/alpha-sda/tk379-ob/futures_usdt/orderbooks/202610/{COIN}_USDT-*.csv.gz')):
    for line in gzip.open(fn, 'rt'):
        p = line.rstrip().split(',')
        t = float(p[0])
        if not (D0 <= t < D0 + ND * 86400): continue
        k = p[1]
        ev.append((t, 1 if k == 'set' else 0, 0, float(p[2]), float(p[3]) * (1 if k != 'take' else -1)))
ev.sort(key=lambda e: (e[0], e[1] != 1))   # снимок раньше событий той же секунды
print(COIN, MODE, 'events', len(ev), 'tick', tick, flush=True)
MK, TK = -1.0, 7.5
def sim(k, T, queue):
    book = {}; lastset = None; pos = 0; ent = 0.0; te = 0.0; nin = nf = 0; rts = []
    ob = os_ = None   # заявки: (ix, qahead, accum)
    for t, kd, sg, p, sz in ev:
        i = int(t - D0)
        if kd == 1:
            if t != lastset: book = {}; lastset = t
            book[round(p / tick)] = sz; continue
        if kd == 0:
            ix = round(p / tick); v = book.get(ix, 0.0) + sz; book[ix] = v if v > 0 else 0.0; continue
        if pos and t >= te:
            j = min(int(te - D0), ND * 86400 - 1); b, a = bid1[j], ask1[j]
            if pos == 1 and b == b: x = (b / ent - 1) * 1e4 - MK - TK
            elif pos == -1 and a == a: x = (ent / a - 1) * 1e4 - MK - TK
            else: x = None
            if x is not None: rts.append((int(te - D0) // 86400, x)); nf += 1
            pos = 0; ob = os_ = None
        if i < 1: continue
        dxv = dx1[i - 1]; b = bid1[i - 1]; a = ask1[i - 1]
        if dxv != dxv or b != b or a != a: ob = os_ = None; continue
        bi = round(b / tick); ai = round(a / tick)
        pb = int(np.floor(dxv * (1 - k) / tick + 1e-9)); ps = int(np.ceil(dxv * (1 + k) / tick - 1e-9))
        if pos <= 0:
            if pb < ai:
                if ob is None or ob[0] != pb:
                    qa = sum(book.get(x, 0.0) for x in range(pb, bi + 1)) if pb <= bi else 0.0
                    ob = (pb, qa, 0.0)
            else: ob = None
        else: ob = None
        if pos >= 0:
            if ps > bi:
                if os_ is None or os_[0] != ps:
                    qa = sum(book.get(x, 0.0) for x in range(ai, ps + 1)) if ps >= ai else 0.0
                    os_ = (ps, qa, 0.0)
            else: os_ = None
        else: os_ = None
        ix = round(p / tick)
        if sg < 0 and ob is not None:      # taker-продажа ходит по бидам
            if ix < ob[0] or (not queue and ix <= ob[0]): fill = True
            elif ix == ob[0]:
                acc = ob[2] + sz; ob = (ob[0], ob[1], acc); fill = acc > ob[1]
            else: fill = False
            if fill:
                if pos == 0: pos = 1; ent = ob[0] * tick; te = t + T; nin += 1; os_ = None
                elif pos == -1 and ob[0] * tick < ent:
                    rts.append((i // 86400, (ent / (ob[0] * tick) - 1) * 1e4 - 2 * MK)); pos = 0; os_ = None
                ob = None
        elif sg > 0 and os_ is not None:
            if ix > os_[0] or (not queue and ix >= os_[0]): fill = True
            elif ix == os_[0]:
                acc = os_[2] + sz; os_ = (os_[0], os_[1], acc); fill = acc > os_[1]
            else: fill = False
            if fill:
                if pos == 0: pos = -1; ent = os_[0] * tick; te = t + T; nin += 1; ob = None
                elif pos == 1 and os_[0] * tick > ent:
                    rts.append((i // 86400, (os_[0] * tick / ent - 1) * 1e4 - 2 * MK)); pos = 0; ob = None
                os_ = None
    return nin, nf, rts
hrs = int(np.sum((dx == dx) & (bd == bd))) * B / 3600
rows = []
for k in (0.002, 0.005, 0.01):
    for T in (300, 1800):
        for queue in (1, 0):
            nin, nf, rts = sim(k, T, queue)
            days = [sum(x for d_, x in rts if d_ == dd) for dd in range(ND)]; cnt = [sum(1 for d_, x in rts if d_ == dd) for dd in range(ND)]
            tot = sum(x for _, x in rts)
            rows.append(dict(coin=COIN, mode=MODE, k_pct=k * 100, T_s=T, queue=queue, hours=round(hrs, 1), entries=nin, roundtrips=len(rts), forced=nf,
                             pnl_sum=round(tot, 1), pnl_per_rt=round(tot / len(rts), 1) if rts else '', pnl_per_h=round(tot / hrs, 2) if hrs else '',
                             days_pos=sum(1 for dd in range(ND) if cnt[dd] and days[dd] > 0), days_n=sum(1 for c in cnt if c),
                             day_pnl='|'.join(f'{x:.0f}' for x in days), day_n='|'.join(map(str, cnt))))
            print(rows[-1], flush=True)
with open(OUT, 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
