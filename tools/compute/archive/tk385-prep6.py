#!/usr/bin/env python3
"""TK-385: кэш событий Gate по часам для tk385-yorsh6.py (память: поток по часам вместо всех 15–22.10 сразу).
Запуск: tk385-prep6.py COIN NPROC. Выход: /data/tk0380/tk385/cache/COIN/hNNN.npy (структурный массив t,ix,kd,sg,sz) + tick.txt.
kd: 0 = книга (сумма +make/−take по (секунда, цена) — агрегат внутри секунды; метки ob целые секунды), 1 = снимок set, 2 = сделка (sg ±1).
Порядок внутри t: set, сделка, книга. Ограничение v6: клэмп нуля книги — по сумме секунды, а не по каждому событию."""
import sys, os, csv, gzip, glob, datetime
import numpy as np
from multiprocessing import Pool
COIN, NP = sys.argv[1], int(sys.argv[2])
D0 = int(datetime.datetime(2026, 10, 15, tzinfo=datetime.timezone.utc).timestamp()); ND = 8; NH = int(os.environ.get('PREP_H', ND * 24))
OUT = f'/data/tk0380/tk385/cache/{COIN}'; os.makedirs(OUT, exist_ok=True)
DT = np.dtype([('t', 'f8'), ('ix', 'i4'), ('kd', 'i1'), ('sg', 'i1'), ('sz', 'f8')])
d = f'/srv/alpha-sda/tk372-gate/raw/fut/{COIN}_USDT'; tt = []; tp = []; ts = []
for fn in sorted(os.listdir(d)):
    for r in csv.DictReader(gzip.open(f'{d}/{fn}', 'rt')):
        t = float(r['create_time_ms'] or r['create_time'])
        if D0 <= t < D0 + ND * 86400: tt.append(t); tp.append(float(r['price'])); ts.append(float(r['size']))
tt = np.array(tt); tp = np.array(tp); ts = np.array(ts)
trp = sorted(set(tp[:20000].tolist())); tick = min(round(b - a, 12) for a, b in zip(trp, trp[1:]) if b - a > 1e-12)
open(f'{OUT}/tick.txt', 'w').write(repr(tick))
tr = np.empty(len(tt), DT); tr['t'] = tt; tr['ix'] = np.round(tp / tick); tr['kd'] = 2; tr['sg'] = np.where(ts > 0, 1, -1); tr['sz'] = np.abs(ts)
o = np.argsort(tr['t'], kind='stable'); tr = tr[o]; th = ((tr['t'] - D0) // 3600).astype(int); del tt, tp, ts, o
def parse(h):
    fn = f'/srv/alpha-sda/tk379-ob/futures_usdt/orderbooks/202610/{COIN}_USDT-{datetime.datetime.fromtimestamp(D0 + h * 3600, datetime.timezone.utc):%Y%m%d%H}.csv.gz'
    if not os.path.exists(fn): return h, np.empty(0, DT)
    b = gzip.open(fn, 'rb').read().replace(b',make,', b',1,').replace(b',take,', b',2,').replace(b',set,', b',0,').replace(b'\n', b',')
    a = np.fromstring(b, sep=',').reshape(-1, 6); del b
    t = a[:, 0]; k = a[:, 1].astype(np.int8); ix = np.round(a[:, 2] / tick).astype(np.int32); sz = a[:, 3]
    m = (t >= D0) & (t < D0 + ND * 86400); t, k, ix, sz = t[m], k[m], ix[m], sz[m]
    s = k == 0; e = ~s
    te, ie, ze = t[e], ix[e], np.where(k[e] == 1, sz[e], -sz[e])
    o = np.lexsort((ie, te)); te, ie, ze = te[o], ie[o], ze[o]
    if len(te):
        g = np.flatnonzero(np.r_[True, (te[1:] != te[:-1]) | (ie[1:] != ie[:-1])])
        te, ie, ze = te[g], ie[g], np.add.reduceat(ze, g)
    out = np.empty(int(s.sum()) + len(te), DT); n = int(s.sum())
    out['t'][:n] = t[s]; out['ix'][:n] = ix[s]; out['kd'][:n] = 1; out['sg'][:n] = 0; out['sz'][:n] = sz[s]
    out['t'][n:] = te; out['ix'][n:] = ie; out['kd'][n:] = 0; out['sg'][n:] = 0; out['sz'][n:] = ze
    return h, out
RANK = np.array([2, 0, 1])   # kd 0 книга, 1 set, 2 сделка → порядок set < сделка < книга
carry = {}; tot = 0
with Pool(NP) as pool:
    for h, ob in pool.imap(parse, range(NH)):
        T = ob['t']; own = ((T - D0) // 3600).astype(int)
        carry[h + 1] = ob[own > h]; ob = ob[own <= h]
        parts = [ob, tr[th == h]]
        if h in carry: parts.append(carry.pop(h))
        a = np.concatenate(parts); a = a[np.lexsort((RANK[a['kd']], a['t']))]
        np.save(f'{OUT}/h{h:03d}.npy', a); tot += len(a)
        if h % 24 == 23: print('day', h // 24, 'events', tot, flush=True)
open(f'{OUT}/DONE', 'w').write(str(tot)); print('done', tot, 'tick', tick)
