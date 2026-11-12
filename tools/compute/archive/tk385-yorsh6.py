#!/usr/bin/env python3
"""TK-385 шаг 2 v3 (формы (г) + BSC-тяжеловесы; копия tk385-yorsh3.py). FORM: sym | asym (при pos=0 только сторона схождения: покупка при mid<DEX, продажа при mid>DEX) | spr (k = множитель × относительный спред BBO, не меньше 5 бп). Только queue=1.
Запуск: tk385-yorsh6.py COIN (кэш — tk385-prep6.py; память: поток по часам, события книги агрегированы по секунде×цене)
Запуск: tk385-yorsh4.py COIN CHAIN MODE FORM OUT.csv.
[v2: «ёршик» с очередью по L2 Gate (TK-379: set = снимок раз в час, make/take = +/- объём на цене) и чистой серединой пула DEX (EVM, как tk386-converge).
Запуск: tk385-yorsh3.py COIN CHAIN MODE(raw|med) OUT.csv. Котировки DEX*(1∓k), округление к тику внутрь стакана; заявка стоит, пока округлённая цена не сменилась (смена = потеря очереди).
Исполнение: сделка Gate строго сквозь цену — заполнение; на цене — копим объём после постановки, заполнение когда накоплено > объёма впереди (стакан на момент постановки; отмены впереди не учтены).
queue=0 — без очереди (касание). N=1 лот. Выход: встречная котировка (цена хуже входа не берём) либо рынок через T. Издержки как в yorsh2: ребейт мейкера 1 бп, тейкер 7,5 бп."""
import sys, os, csv, gzip, json, glob, datetime
import numpy as np
from array import array
COIN, CH, MODE, FORM, OUT = sys.argv[1:6]
B = 10; D0 = int(datetime.datetime(2026, 10, 15, tzinfo=datetime.timezone.utc).timestamp()); ND = 8; N = ND * 86400 // B; AGE = 6
EVM = {'SUE': dict(pool='0xfFCa411CFbcD3194503eFfA80Fd394b55387915c', q0=False, dt=18, dq=18, quote='BNB'),
       'ZEC': dict(pool='0x6701B287c8170D6c290C6f4Ef426782bD61Ae310', q0=False, dt=18, dq=18, quote=None, v3=True),
       'BTR': dict(pool='0x787ABD73593Bed588852163402d191C302c7f8b6', q0=True, dt=18, dq=18, quote=None, v3=True),
       'Q': dict(pool='0x8Bafe0bdD3eB9ae0539f5B32e771C1a72A189b7f', q0=True, dt=18, dq=18, quote=None, v3=True),
       'DGAI': dict(pool='0x0e6E1F47C64f517fc9dC35698718C7Dfc708D534', q0=False, dt=18, dq=18, quote=None, v3=True),
       'CAKE': dict(pool='0xAfB2Da14056725E3BA3a30dD846B6BBbd7886c56', q0=False, dt=18, dq=18, quote='BNB', v3=True),
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
CD = f'/data/tk0380/tk385/cache/{COIN}'; tick = float(open(f'{CD}/tick.txt').read()); NEV = 0
def evit(CH=1 << 20):   # поток по часам из кэша (tk385-prep6.py), mmap
    for h in range(ND * 24):
        a = np.load(f'{CD}/h{h:03d}.npy', mmap_mode='r')
        for c in range(0, len(a), CH):
            x = a[c:c + CH]; yield from zip(x['t'].tolist(), x['kd'].tolist(), x['sg'].tolist(), x['ix'].tolist(), x['sz'].tolist())
print(COIN, MODE, 'tick', tick, flush=True)
MK, TK = -1.0, 7.5
def sim(k, T, queue):
    kk = k
    book = {}; lastset = None; pos = 0; ent = 0.0; te = 0.0; nin = nf = 0; rts = []
    ob = os_ = None   # заявки: (ix, qahead, accum)
    for t, kd, sg, ix, sz in evit():
        i = int(t - D0)
        if kd == 1:
            if t != lastset: book = {}; lastset = t
            book[ix] = sz; continue
        if kd == 0:
            v = book.get(ix, 0.0) + sz; book[ix] = v if v > 0 else 0.0; continue
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
        if FORM == 'spr': k = max(0.0005, kk * (a - b) / ((a + b) / 2))
        pb = int(np.floor(dxv * (1 - k) / tick + 1e-9)); ps = int(np.ceil(dxv * (1 + k) / tick - 1e-9))
        if pos <= 0:
            if pb < ai:
                if ob is None or ob[0] != pb:
                    qa = sum(book.get(x, 0.0) for x in range(pb, bi + 1)) if pb <= bi else 0.0
                    ob = (pb, qa, 0.0)
            else: ob = None
            if FORM == 'asym' and pos == 0 and (a + b) / 2 >= dxv: ob = None
        else: ob = None
        if pos >= 0:
            if ps > bi:
                if os_ is None or os_[0] != ps:
                    qa = sum(book.get(x, 0.0) for x in range(ai, ps + 1)) if ps >= ai else 0.0
                    os_ = (ps, qa, 0.0)
            else: os_ = None
            if FORM == 'asym' and pos == 0 and (a + b) / 2 <= dxv: os_ = None
        else: os_ = None
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
rows = []; KI = os.environ.get('KI'); ci = -1   # KI=0..5 — одна пара (k,T) на задание (15 ядер)
for k in ((0.5, 1.0, 2.0) if FORM == 'spr' else (0.002, 0.005, 0.01)):
    for T in (300, 1800):
        ci += 1
        if KI is not None and int(KI) != ci: continue
        for queue in (1,):
            nin, nf, rts = sim(k, T, queue)
            days = [sum(x for d_, x in rts if d_ == dd) for dd in range(ND)]; cnt = [sum(1 for d_, x in rts if d_ == dd) for dd in range(ND)]
            tot = sum(x for _, x in rts)
            rows.append(dict(coin=COIN, mode=MODE, form=FORM, k_pct=(k if FORM == 'spr' else k * 100), T_s=T, queue=queue, hours=round(hrs, 1), entries=nin, roundtrips=len(rts), forced=nf,
                             pnl_sum=round(tot, 1), pnl_per_rt=round(tot / len(rts), 1) if rts else '', pnl_per_h=round(tot / hrs, 2) if hrs else '',
                             days_pos=sum(1 for dd in range(ND) if cnt[dd] and days[dd] > 0), days_n=sum(1 for c in cnt if c),
                             day_pnl='|'.join(f'{x:.0f}' for x in days), day_n='|'.join(map(str, cnt))))
            print(rows[-1], flush=True)
with open(OUT, 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
