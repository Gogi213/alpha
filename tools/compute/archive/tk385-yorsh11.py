#!/usr/bin/env python3
"""TK-385 yorsh11 = yorsh10 без заглядывания (reviews/tk385-yorsh-y12 п.1-3): ряды BBO и DEX посекундные (метка = секунда, ffill ≤60 с, на секунде t видны только метки ≤ t-1-LAG-DELAY), тариф MK/TK из env (по умолчанию 1.3/3.25 бп = VIP0 2/5 × 0.65, CEO 23:55) + колонка pnl_vip0_per_rt (2/5 бп), SHIFT_S (с) вместо SHIFT_H (отрицательный = DEX из будущего, контроль), лот LOTX обязателен в запуске.
TK-385 yorsh10 = yorsh9 + PESS=2 (reviews/tk385-yorsh-y11 п.1): котировка внутри спреда сдвигается на лучшую цену своей стороны (bid: min(pb, лучший бид); ask: max(ps, лучший аск)), встаёт в хвост очереди этого уровня (объём впереди = L2 на момент выставления, убывает принтами по уровню), заполнение — накопленные принты по уровню > очередь + лот, либо принт сквозь; колонки adv60/adv300 — средний ход Gate mid через 60/300 с после входа в пользу позиции, бп. Отмены по уровню в L2 не учтены (оценка верхняя).
TK-385 yorsh9 = yorsh8 + env PESS=1 (пессимистичная очередь Судьи, reviews/tk385-yorsh-y10): заполнение только принтом строго сквозь цену заявки (касание цены не заполняет; заявка внутри спреда = в хвосте, сквозь-принт обязателен).
TK-385 yorsh8 = yorsh7 + env NULL_MIN (нуль Судьи: линия = скользящая средняя Gate mid за NULL_MIN мин вместо DEX, только в часы с DEX), QMULT (очередь ×), DELAY_S (задержка ордера: котировка и стакан устарели на DELAY_S с); колонка day_h (часы по суткам).
TK-385 yorsh7 = yorsh6 + контроли Судьи (reviews/tk385-yorsh-v3): env SHIFT_H (сдвиг линии DEX в часах, нуль), LAG_S (лаг DEX, с), LOTX (лот = LOTX × медиана принта: сквозной принт мельче лота не заполняет; у цены — накоплено > очередь + лот); выходы лимит/рынок раздельно; без заполнений принтом > p99.
TK-385 шаг 2 v3 (формы (г) + BSC-тяжеловесы; копия tk385-yorsh3.py). FORM: sym | asym (при pos=0 только сторона схождения: покупка при mid<DEX, продажа при mid>DEX) | spr (k = множитель × относительный спред BBO, не меньше 5 бп). Только queue=1.
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
B = 1; D0 = int(datetime.datetime(2026, 10, 15, tzinfo=datetime.timezone.utc).timestamp()); ND = 8; N = ND * 86400 // B; AGE = 60
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
            elif i - li <= 60: a[i] = l
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
    base = np.full(N, np.nan); W = 86400
    for k in range(W, N, 3600):
        w = gap[k - W:k]; w = w[w == w]
        if len(w) >= 2000: base[k:k + 3600] = np.median(w)
    return base
bd, ak = loadbbo(COIN); mid = np.where(ak > bd, (bd + ak) / 2, np.nan)
bq = None
if EVM[COIN]['quote'] == 'BNB': q = loadbbo('BNB'); bq = (q[0] + q[1]) / 2
dx = evm_mid(COIN, bq)
SHIFT_S = int(os.environ.get('SHIFT_S', 0)); SHIFT_H = SHIFT_S / 3600; LAG_S = int(os.environ.get('LAG_S', 0)); LOTX = float(os.environ.get('LOTX', 0))
NULL_MIN = float(os.environ.get('NULL_MIN', 0)); QMULT = float(os.environ.get('QMULT', 1)); DELAY_S = int(os.environ.get('DELAY_S', 0)); PESS = int(os.environ.get('PESS', 0)); Q2 = PESS == 2; PESS = 1 if PESS == 1 else 0
if NULL_MIN:
    w = max(1, int(NULL_MIN * 60)); m0 = np.where(mid == mid, mid, 0.0); c1 = np.cumsum(m0); cn = np.cumsum((mid == mid).astype(float))
    s1 = c1 - np.concatenate([np.zeros(w), c1[:-w]]); sn = cn - np.concatenate([np.zeros(w), cn[:-w]])
    ma = np.where(sn > 0, s1 / np.maximum(sn, 1), np.nan); dx = np.where(dx == dx, ma, np.nan)
if SHIFT_S:
    sh = SHIFT_S; dx = np.roll(dx, sh)
    if sh > 0: dx[:sh] = np.nan
    else: dx[sh:] = np.nan
gap = np.where((mid == mid) & (dx == dx), np.log(mid / dx) * 1e4, np.nan)
if MODE == 'med': dx = dx * np.exp(rollbase(gap) / 1e4)   # до 16.10 NaN
mid1 = mid; dx1 = dx; bid1 = bd; ask1 = ak
CD = f'/data/tk0380/tk385/cache/{COIN}'; tick = float(open(f'{CD}/tick.txt').read()); NEV = 0
def evit(CH=1 << 20):   # поток по часам из кэша (tk385-prep6.py), mmap
    for h in range(ND * 24):
        a = np.load(f'{CD}/h{h:03d}.npy', mmap_mode='r')
        for c in range(0, len(a), CH):
            x = a[c:c + CH]; yield from zip(x['t'].tolist(), x['kd'].tolist(), x['sg'].tolist(), x['ix'].tolist(), x['sz'].tolist())
_s = []
for _h in range(0, 48, 2):
    _a = np.load(f'{CD}/h{_h:03d}.npy', mmap_mode='r'); _s.append(np.asarray(_a['sz'][_a['kd'] == 2][:200000]))
_s = np.concatenate(_s); MED = float(np.median(_s)); P99 = float(np.quantile(_s, 0.99)); LOT = LOTX * MED
print(COIN, MODE, 'tick', tick, 'median print', MED, 'p99', P99, 'lot', LOT, 'shift_h', SHIFT_H, 'lag_s', LAG_S, flush=True)
MK = float(os.environ.get('MK', 1.3)); TK = float(os.environ.get('TK', 3.25))   # бп на ногу: мейкер/тейкер; в rts — валовый ход (комиссии ниже)
def sim(k, T, queue):
    kk = k; epr = 0.0
    adv = []; book = {}; lastset = None; pos = 0; ent = 0.0; te = 0.0; nin = nf = 0; rts = []; bigfill = 0
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
            if pos == 1 and b == b: x = (b / ent - 1) * 1e4
            elif pos == -1 and a == a: x = (ent / a - 1) * 1e4
            else: x = None
            if x is not None: rts.append((int(te - D0) // 86400, x, 'M', epr)); nf += 1
            pos = 0; ob = os_ = None; epr = 0.0
        if i < 1: continue
        dxv = dx1[i - 1 - LAG_S - DELAY_S] if i - 1 - LAG_S - DELAY_S >= 0 else np.nan; b = bid1[i - 1 - DELAY_S]; a = ask1[i - 1 - DELAY_S]
        if dxv != dxv or b != b or a != a: ob = os_ = None; continue
        bi = round(b / tick); ai = round(a / tick)
        if FORM == 'spr': k = max(0.0005, kk * (a - b) / ((a + b) / 2))
        pb = int(np.floor(dxv * (1 - k) / tick + 1e-9)); ps = int(np.ceil(dxv * (1 + k) / tick - 1e-9))
        if Q2: pb = min(pb, bi); ps = max(ps, ai)
        if pos <= 0:
            if pb < ai:
                if ob is None or ob[0] != pb:
                    qa = sum(book.get(x, 0.0) for x in range(pb, bi + 1)) if pb <= bi else 0.0
                    ob = (pb, qa * QMULT, 0.0)
            else: ob = None
            if FORM == 'asym' and pos == 0 and (a + b) / 2 >= dxv: ob = None
        else: ob = None
        if pos >= 0:
            if ps > bi:
                if os_ is None or os_[0] != ps:
                    qa = sum(book.get(x, 0.0) for x in range(ai, ps + 1)) if ps >= ai else 0.0
                    os_ = (ps, qa * QMULT, 0.0)
            else: os_ = None
            if FORM == 'asym' and pos == 0 and (a + b) / 2 <= dxv: os_ = None
        else: os_ = None
        if sg < 0 and ob is not None:      # taker-продажа ходит по бидам
            if ix < ob[0] or (not queue and ix <= ob[0] and not PESS): fill = sz >= LOT
            elif ix == ob[0] and not PESS:
                acc = ob[2] + sz; ob = (ob[0], ob[1], acc); fill = acc > ob[1] + LOT
            else: fill = False
            if fill:
                if pos == 0: pos = 1; ent = ob[0] * tick; te = t + T; nin += 1; os_ = None; epr = sz; adv.append((1, ent, i))
                elif pos == -1 and ob[0] * tick < ent:
                    rts.append((i // 86400, (ent / (ob[0] * tick) - 1) * 1e4, 'L', max(epr, sz))); pos = 0; os_ = None
                ob = None
        elif sg > 0 and os_ is not None:
            if ix > os_[0] or (not queue and ix >= os_[0] and not PESS): fill = sz >= LOT
            elif ix == os_[0] and not PESS:
                acc = os_[2] + sz; os_ = (os_[0], os_[1], acc); fill = acc > os_[1] + LOT
            else: fill = False
            if fill:
                if pos == 0: pos = -1; ent = os_[0] * tick; te = t + T; nin += 1; ob = None; epr = sz; adv.append((-1, ent, i))
                elif pos == 1 and os_[0] * tick > ent:
                    rts.append((i // 86400, (os_[0] * tick / ent - 1) * 1e4, 'L', max(epr, sz))); pos = 0; ob = None
                os_ = None
    a60 = []; a300 = []
    for sd, e, i0 in adv:
        for h, L_ in ((60, a60), (300, a300)):
            j = i0 + h
            if j < ND * 86400:
                m = mid1[j]
                if m == m: L_.append(sd * (m / e - 1) * 1e4)
    return nin, nf, rts, (float(np.mean(a60)) if a60 else None, float(np.mean(a300)) if a300 else None)
hrs = int(np.sum((dx == dx) & (bd == bd))) * B / 3600
dayh = [float(np.sum((dx[dd*86400:(dd+1)*86400] == dx[dd*86400:(dd+1)*86400]) & (bd[dd*86400:(dd+1)*86400] == bd[dd*86400:(dd+1)*86400]))) * B / 3600 for dd in range(ND)]
rows = []; KI = os.environ.get('KI'); ci = -1   # KI=0..5 — одна пара (k,T) на задание (15 ядер)
for k in ((0.5, 1.0, 2.0) if FORM == 'spr' else (0.002, 0.005, 0.01)):
    for T in (300, 1800):
        ci += 1
        if KI is not None and int(KI) != ci: continue
        for queue in (1,):
            nin, nf, rts, ad = sim(k, T, queue)
            rv = [(r[0], r[1] - (2 * 2.0 if r[2] == 'L' else 2.0 + 5.0), r[2], r[3]) for r in rts]; v0 = sum(r[1] for r in rv)
            rts = [(r[0], r[1] - (2 * MK if r[2] == 'L' else MK + TK), r[2], r[3]) for r in rts]
            days = [sum(r[1] for r in rts if r[0] == dd) for dd in range(ND)]; cnt = [sum(1 for r in rts if r[0] == dd) for dd in range(ND)]
            tot = sum(r[1] for r in rts)
            L = [r[1] for r in rts if r[2] == 'L']; M = [r[1] for r in rts if r[2] == 'M']; nb = [r[1] for r in rts if r[3] <= P99]
            rows.append(dict(coin=COIN, mode=MODE, form=FORM,  lag_s=LAG_S, null_min=NULL_MIN, qmult=QMULT, delay_s=DELAY_S, pess=2 if Q2 else PESS, adv60=round(ad[0], 1) if ad[0] is not None else '', adv300=round(ad[1], 1) if ad[1] is not None else '', lotx=LOTX, lot=round(LOT, 6), k_pct=(k if FORM == 'spr' else k * 100), T_s=T, queue=queue, hours=round(hrs, 1), entries=nin, roundtrips=len(rts), forced=nf,
                             n_limit=len(L), pnl_limit_rt=round(sum(L) / len(L), 1) if L else '', n_market=len(M), pnl_market_rt=round(sum(M) / len(M), 1) if M else '', n_nobig=len(nb), pnl_nobig_rt=round(sum(nb) / len(nb), 1) if nb else '', big_share=round(1 - len(nb) / len(rts), 3) if rts else '', pnl_sum=round(tot, 1), pnl_per_rt=round(tot / len(rts), 1) if rts else '', pnl_vip0_per_rt=round(v0 / len(rts), 1) if rts else '', mk=MK, tk=TK, shift_s=SHIFT_S, pnl_per_h=round(tot / hrs, 2) if hrs else '',
                             days_pos=sum(1 for dd in range(ND) if cnt[dd] and days[dd] > 0), days_n=sum(1 for c in cnt if c),
                             day_pnl='|'.join(f'{x:.0f}' for x in days), day_n='|'.join(map(str, cnt)), day_h='|'.join(f'{x:.1f}' for x in dayh)))
            print(rows[-1], flush=True)
with open(OUT, 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
