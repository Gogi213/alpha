#!/usr/bin/env python3
"""TK-409 блок R, форма с очередью (возврат Судьи 26.10 08:15): резкий уход DEX X=10/15/20 % за W=10 с/1/5/30 мин -> мейкер-вход Gate-фьюча В СТОРОНУ ухода
у лучшей цены (long -> bid, short -> ask), очередь = объём лучшего уровня gev2 на постановке, заполнение по сделкам мс (как (в) в tk409-ev-conv.py), без отмен впереди;
выход рыночный по книге на вход+h+L, h = 60/300 с. Событие видно с tv = ts+1000, заявка действует с tv+L (L=250), A=5 с, окно заявки 300 с.
Фильтр выбросов (правило ДО счёта, причинное): событие остаётся, если к ts уже >= 2 свопа в разных блоках/логах за последние 10 с имеют ход >= X/2 от опорной цены окна W
(одиночный своп в тонком пуле — выброс). Контроль: до 5 случайных свопов той же монеты, того же знака, с 0 < ход < X, тот же вход/выход.
Запуск (calc): GEV=... python tk409-ev-rq.py COIN MIDCSV OUTPREFIX [LEG]   Выход: OUTPREFIX-eps.csv, OUTPREFIX-counts.csv
"""
import sys, os, glob, gzip, csv, datetime as dt
import numpy as np
coin, midf, outp = sys.argv[1:4]
D0 = 1792022400 * 1000; ND = 8; DEND = D0 + ND * 86400000; H = 3600000
VIS, LAT, A, DL = 1000, 250, 5000, 300000
XS = [0.10, 0.15, 0.20]; WS = [10, 60, 300, 1800]; HS = [60, 300]
GEV = os.environ.get('GEV', '/data/tk0393/tk401/gev2all'); GTR = '/srv/alpha-sda/tk372-gate/raw/fut'

sw = np.loadtxt(midf, delimiter=',', dtype=np.float64, ndmin=2) if midf.endswith('.csv') else np.array([[float(x) for x in l.split(',')] for l in gzip.open(midf, 'rt')])
sw = sw[(sw[:, 0] * 1000 >= D0) & (sw[:, 0] * 1000 < DEND)]
sw = sw[np.argsort(sw[:, 0], kind='stable')]
T = (sw[:, 0] * 1000).astype(np.int64); BLK = sw[:, 1].astype(np.int64); LP = np.log(sw[:, 3])
n = len(T)
if n < 2: sys.exit('SKIP: нет свопов')

hv = np.zeros(ND * 24 + 2, bool); parts = []
for f in sorted(glob.glob(f'{GEV}/{coin}/??????????.npy')):
    try: k = int(open(f[:-4] + '.n').read())
    except OSError: k = -1
    if k < 100: continue
    d = dt.datetime.strptime(os.path.basename(f)[:-4], '%Y%m%d%H').replace(tzinfo=dt.timezone.utc)
    hi = int(d.timestamp()) // 3600 - D0 // H
    if 0 <= hi < ND * 24: hv[hi] = True; parts.append(np.load(f))
if not parts: sys.exit('SKIP: нет gev2')
a = np.concatenate(parts); a = a[np.argsort(a[:, 0], kind='stable')]
GT = a[:, 0].astype(np.int64); GB, GA, GBQ, GAQ = a[:, 1], a[:, 2], a[:, 3], a[:, 4]
GOK = np.isfinite(GB) & np.isfinite(GA) & (GB < GA)
tp = []
for d in range(ND):
    day = (dt.datetime(2026, 10, 15, tzinfo=dt.timezone.utc) + dt.timedelta(days=d)).strftime('%Y-%m-%d')
    fn = f'{GTR}/{coin}_USDT/{day}.csv.gz'
    if os.path.exists(fn):
        x = np.loadtxt(fn, delimiter=',', skiprows=1, usecols=(3, 4, 5), dtype=np.float64, ndmin=2)
        if len(x): tp.append(x)
if not tp: sys.exit('SKIP: нет сделок')
tr = np.concatenate(tp); tr = tr[np.argsort(tr[:, 0], kind='stable')]
TT = np.round(tr[:, 0] * 1000).astype(np.int64); TSZ = tr[:, 1]; TPX = tr[:, 2]

def side_fill(side, p, ahead, t0, t1):
    i0 = int(np.searchsorted(TT, t0, side='left')); i1 = int(np.searchsorted(TT, t1, side='left'))
    if i1 <= i0: return None
    sz = TSZ[i0:i1]; pxs = TPX[i0:i1]; t = TT[i0:i1]
    if side > 0: m = sz < 0; thru = m & (pxs < p - 1e-12 * p)
    else: m = sz > 0; thru = m & (pxs > p + 1e-12 * p)
    lvl = m & (np.abs(pxs - p) <= 1e-9 * p); best = None
    ti = np.nonzero(thru)[0]
    if len(ti): best = int(t[ti[0]])
    li = np.nonzero(lvl)[0]
    if len(li):
        ok = np.nonzero(np.cumsum(np.abs(sz[li])) >= ahead + 1)[0]
        if len(ok) and (best is None or int(t[li[ok[0]]]) < best): best = int(t[li[ok[0]]])
    return best
def gate_at(t):
    k = int(np.searchsorted(GT, t, side='right')) - 1; kc = max(k, 0)
    hi_t = min(max((t - D0) // H, 0), ND * 24 - 1); hi_r = min(max((GT[kc] - D0) // H, 0), ND * 24 - 1)
    return kc, bool((k >= 0) and (t - GT[kc] <= A) and hv[hi_t] and hv[hi_r] and GOK[kc])
def mk_entry(t, q):
    k, ok = gate_at(t + LAT)
    if not ok: return 'drop_age'
    p, ah = (GB[k], GBQ[k]) if q > 0 else (GA[k], GAQ[k])
    tf = side_fill(q, p, ah, t + LAT, t + LAT + DL)
    return 'unfilled' if tf is None else (tf, float(p))
def trade(t, q, h):
    r = mk_entry(t, q)
    if isinstance(r, str): return r
    tin, pe = r; k, ok = gate_at(tin + h * 1000 + LAT)
    if not ok: return 'drop_exit_age'
    px, qty = (GB[k], GBQ[k]) if q > 0 else (GA[k], GAQ[k])   # закрытие: long продаёт по bid
    if qty < 1: return 'exit_thin'
    return q * (np.log(px) - np.log(pe)) * 1e4

eps = []; counts = []
for W in WS:
    Wm = W * 1000
    j = np.searchsorted(T, T - Wm, side='right') - 1
    hasj = (j >= 0) & (T[np.clip(j, 0, None)] >= T - 2 * Wm)
    mv = np.where(hasj, LP - LP[np.clip(j, 0, None)], np.nan)
    for xi, X in enumerate(XS):
        thr = np.log1p(X)
        for s, nm in ((1, 'up'), (-1, 'down')):
            cand = np.flatnonzero(hasj & (s * mv >= thr)); ev = []; last = -1
            for i in cand:
                if T[i] >= last: ev.append(i); last = T[i] + Wm
            pool = np.flatnonzero(hasj & (s * mv > 0) & (s * mv < thr))
            rng = np.random.default_rng([409, W, xi, s + 1])
            c = dict(n_event=len(ev), outlier=0, drop_age=0, unfilled=0, drop_exit_age=0, exit_thin=0, ok=0)
            for i in ev:
                # причинный фильтр выбросов: >= 2 свопа в разных блоках за последние 10 с (включая i) с ходом >= X/2 от опорной цены окна
                w0 = int(np.searchsorted(T, T[i] - 10000, side='left')); ref = LP[j[i]]
                m = np.flatnonzero(s * (LP[w0:i + 1] - ref) >= thr / 2) + w0
                if len(set(BLK[m].tolist())) < 2: c['outlier'] += 1; continue
                for h in HS:
                    g = trade(int(T[i]) + VIS, s, h)
                    if isinstance(g, str): c[g] += 1 if h == HS[0] else 0; continue
                    cg = []
                    for r in (rng.choice(pool, 5) if len(pool) else []):
                        x = trade(int(T[r]) + VIS, s, h)
                        if not isinstance(x, str): cg.append(x)
                    if h == HS[0]: c['ok'] += 1
                    eps.append((coin, X, W, nm, int(T[i]) // 1000, (int(T[i]) - D0) // 86400000, h, round(float(g), 3), round(float(np.mean(cg)), 3) if cg else ''))
            counts.append((coin, X, W, nm) + tuple(c.values()))
with open(outp + '-eps.csv', 'w', newline='') as f: w = csv.writer(f); w.writerow('coin X W dir ts day h gross_bp ctrl_bp'.split()); w.writerows(eps)
with open(outp + '-counts.csv', 'w', newline='') as f: w = csv.writer(f); w.writerow('coin X W dir n_event outlier drop_age unfilled drop_exit_age exit_thin ok'.split()); w.writerows(counts)
print(coin, 'swaps', n, 'eps', len(eps))
