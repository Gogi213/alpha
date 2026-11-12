#!/usr/bin/env python3
"""TK-409 блок R (П-30 §11, добавка владельца 26.10): резкие уходы DEX X=10/15/20 % за W=10 с/1/5/30 мин и отклик Gate (фьюч, спот) «против отрыва».
Основа tk407-ev.py. Свопа видим с tv = ts+VIS (1000), вход Gate по gev2 на tv+L (L=250) — te = ts+1250; A=5 с (живость, дыры — часы). Горизонты h от te.
Контроль: случайные свопы той же монеты со знаком s и ходом 0<|ход|<X, то же удержание. Валовый ход, бп; нетто не считается (критерий владельца).
Запуск (calc): GEV=/data/tk0393/tk401/gev2all python tk409-ev-r.py COIN MIDCSV OUTPREFIX
Выход: OUTPREFIX-curve.csv, -counts.csv, -events.csv (все события X>=10 %, W=10 с..30 мин: ход и середина Gate фьюч/спот на -60/-10/0/+10/+60/+300 с от ts).
"""
import sys, os, gzip, csv, glob, datetime as dt
import numpy as np
coin, midf, outp = sys.argv[1:4]
LEG = sys.argv[4] if len(sys.argv) > 4 else ''
D0 = 1792022400 * 1000; ND = 8; DEND = D0 + ND * 86400000
HS = [1, 5, 10, 30, 60, 120, 300, 600, 1800, 3600]; VIS = 1000; LAT = 250; OFFS = [-60, -10, 0, 10, 60, 300]
XS = [0.10, 0.15, 0.20]; WS = [10, 60, 300, 1800]; AS = [5000]; ALAB = {5000: '5'}
GEV = os.environ.get('GEV', '/data/tk0402/gev2'); TR = '/srv/alpha-sda/tk372-gate/raw'
HDR = 'coin mkt A X W dir day h n_ev n_h dex_move_bp resp_bp ctrl_bp excess_bp share pre_bp'.split()
CH = 'coin mkt A X W dir n_swaps no_window n_cand n_event drop_hole drop_age n_ok n_ctrl_pool coin_days'.split()

sw = np.loadtxt(midf, delimiter=',', dtype=np.float64, ndmin=2) if midf.endswith('.csv') else None
if sw is None:
    rows = [l.split(',') for l in gzip.open(midf, 'rt')]
    sw = np.array([[float(x) for x in r] for r in rows])
sw = sw[(sw[:, 0] * 1000 >= D0) & (sw[:, 0] * 1000 < DEND)]
T = (sw[:, 0] * 1000).astype(np.int64); P = sw[:, 3]; BLK = sw[:, 1].astype(np.int64); LOGI = sw[:, 2].astype(np.int64); LP = np.log(P)
if LEG:  # TK-407: цена в монете-ноге (BNB/ETH) -> USD по последней сделке Gate спот LEG_USDT <= t, возраст <= 30 с, иначе своп вне
    _d = f'/srv/alpha-sda/tk372-gate/raw/spot/{LEG}_USDT'; _t = []; _p = []
    for fn in sorted(os.listdir(_d)):
        try:
            for r in csv.DictReader(gzip.open(f'{_d}/{fn}', 'rt')):
                v = float(r.get('create_time_ms') or r['create_time']); _t.append(round(v if v > 1e11 else v * 1000)); _p.append(float(r['price']))
        except EOFError: pass
    _t = np.array(_t, np.int64); _p = np.array(_p); _o = np.argsort(_t, kind='stable'); _t = _t[_o]; _p = _p[_o]
    _k = np.searchsorted(_t, T, side='right') - 1; _kc = np.clip(_k, 0, None); _ok = (_k >= 0) & (T - _t[_kc] <= 30000)
    sw = sw[_ok]; T = T[_ok]; P = P[_ok] * _p[_kc[_ok]]; BLK = BLK[_ok]; LOGI = LOGI[_ok]; LP = np.log(P)

TE = T + VIS + LAT

def gate_fut():
    fs = sorted(glob.glob(f'{GEV}/{coin}/??????????.npy')); good = []
    for f in fs:
        try: n = int(open(f[:-4] + '.n').read())
        except OSError: n = -1
        if n >= 100: good.append(f)
    if not good: return None
    a = np.concatenate([np.load(f)[:, :3] for f in good]); a = a[np.argsort(a[:, 0], kind='stable')]
    hv = set()
    for f in good:
        b = os.path.basename(f)[:-4]; d = dt.datetime.strptime(b, '%Y%m%d%H').replace(tzinfo=dt.timezone.utc); hv.add(int(d.timestamp()) // 3600)
    return a[:, 0].astype(np.int64), (a[:, 1] + a[:, 2]) / 2, hv
def gate_spot():
    sd = f'{TR}/spot/{coin}_USDT'
    if not os.path.isdir(sd): return None
    tt = []; pp = []
    for fn in sorted(os.listdir(sd)):
        try:
            for r in csv.DictReader(gzip.open(f'{sd}/{fn}', 'rt')):
                v = float(r.get('create_time_ms') or r['create_time']); tt.append(round(v if v > 1e11 else v * 1000)); pp.append(float(r['price']))
        except EOFError: pass
    if not tt: return None
    t = np.array(tt, np.int64); p = np.array(pp); o = np.argsort(t, kind='stable'); t = t[o]; p = p[o]
    hv = set(range(D0 // 3600000, DEND // 3600000))  # сделки: дыр нет (возраст A — настоящая устарелость)
    return t, p, hv

def gval(g, t, A):
    gt, gm, hv = g
    k = np.searchsorted(gt, t, side='right') - 1; kc = np.clip(k, 0, None)
    age = t - gt[kc]
    h_t = t // 3600000; h_r = gt[kc] // 3600000
    hok = np.array([x in hv for x in h_t]) & np.array([x in hv for x in h_r]) if len(t) else np.zeros(0, bool)
    ok = (k >= 0) & (age <= A) & hok
    return np.where(ok, gm[kc], np.nan), ok, (k >= 0) & ~hok, (k >= 0) & hok & (age > A)

markets = {}
for name, fn in (('fut', gate_fut), ('spot', gate_spot)):
    g = fn()
    if g is not None: markets[name] = g
if not markets: sys.exit('SKIP: нет данных Gate')

curve = []; counts = []
n = len(T)
for W in WS:
    Wm = W * 1000
    j = np.searchsorted(T, T - Wm, side='right') - 1
    hasj = (j >= 0) & (T[np.clip(j, 0, None)] >= T - 2 * Wm)
    jj = np.clip(j, 0, None); mv = np.where(hasj, LP - LP[jj], np.nan)
    for mi, (mkt, g) in enumerate(markets.items()):
        for ai, A in enumerate(AS):
            G0, ok0, hole0, age0 = gval(g, TE, A)
            Gp, okp, _, _ = gval(g, T - Wm, A)
            lG0 = np.log(G0); pre_all = np.log(G0) - np.log(Gp)
            # отклик на всех горизонтах для всех свопов — лениво, по подмножествам
            def resp(idx, s):
                out = np.full((len(idx), len(HS)), np.nan)
                for k, h in enumerate(HS):
                    G1, ok1, _, _ = gval(g, TE[idx] + h * 1000, A)
                    out[:, k] = s * (np.log(G1) - lG0[idx])
                return out
            for xi, X in enumerate(XS):
                thr = np.log1p(X)
                for si, (s, name) in enumerate(((1, 'up'), (-1, 'down'))):
                    cand = np.flatnonzero(hasj & (s * mv >= thr)); ev = []; last = -1
                    for i in cand:
                        if T[i] >= last: ev.append(i); last = T[i] + Wm
                    ev = np.array(ev, dtype=np.int64)
                    nev = len(ev); evok = ev[ok0[ev]] if nev else ev
                    pool = np.flatnonzero(hasj & (s * mv > 0) & (s * mv < thr) & ok0)
                    days_ev = (T[evok] - D0) // 86400000
                    counts.append((coin, mkt, ALAB[A], X, W, name, n, int((~hasj).sum()), len(cand), nev, int(hole0[ev].sum()) if nev else 0, int(age0[ev].sum()) if nev else 0, len(evok), len(pool), len(set(days_ev.tolist()))))
                    if len(evok) == 0: continue
                    rng = np.random.default_rng([401, W, xi, si, ai, mi])
                    ctl = rng.choice(pool, size=len(evok), replace=len(pool) < len(evok)) if len(pool) else np.array([], np.int64)
                    R = resp(evok, s); C = resp(ctl, s) if len(ctl) else np.full((0, len(HS)), np.nan)
                    dex = s * mv[evok]; pre = s * pre_all[evok]; days_c = (T[ctl] - D0) // 86400000 if len(ctl) else np.array([], int)
                    for dk in ['all'] + sorted(set(days_ev.tolist())):
                        m = np.ones(len(evok), bool) if dk == 'all' else days_ev == dk
                        mc = np.ones(len(ctl), bool) if dk == 'all' else days_c == dk
                        for k, h in enumerate(HS):
                            r = R[m, k]; v = ~np.isnan(r)
                            if not v.any(): continue
                            c = C[mc, k] if len(ctl) else np.array([]); c = c[~np.isnan(c)]
                            mr = float(r[v].mean()); mc_ = float(c.mean()) if len(c) else np.nan; md = float(dex[m][v].mean())
                            pr = pre[m][v]; pr = pr[~np.isnan(pr)]
                            curve.append((coin, mkt, ALAB[A], X, W, name, dk, h, int(m.sum()), int(v.sum()), round(md * 1e4, 2), round(mr * 1e4, 2),
                                          round(mc_ * 1e4, 2) if mc_ == mc_ else '', round((mr - mc_) * 1e4, 2) if mc_ == mc_ else '',
                                          round((mr - mc_) / md, 4) if mc_ == mc_ and md else '', round(float(pr.mean()) * 1e4, 2) if len(pr) else ''))
with open(outp + '-curve.csv', 'w', newline='') as f: w = csv.writer(f); w.writerow(HDR); w.writerows(curve)
with open(outp + '-counts.csv', 'w', newline='') as f: w = csv.writer(f); w.writerow(CH); w.writerows(counts)

# события для списка: первое пересечение X=10 %, все W; середина Gate на ts+off (A=5 с)
EH = 'coin W dir ts utc move_pct dex_price mkt'.split() + [f'g{o:+d}' for o in OFFS]
erows = []
for W in WS:
    Wm = W * 1000
    j = np.searchsorted(T, T - Wm, side='right') - 1
    hasj = (j >= 0) & (T[np.clip(j, 0, None)] >= T - 2 * Wm)
    mv = np.where(hasj, LP - LP[np.clip(j, 0, None)], np.nan)
    for s_, name in ((1, 'up'), (-1, 'down')):
        cand = np.flatnonzero(hasj & (s_ * mv >= np.log1p(XS[0]))); ev = []; last = -1
        for i in cand:
            if T[i] >= last: ev.append(i); last = T[i] + Wm
        for i in ev:
            for mkt, g in markets.items():
                vals = [gval(g, np.array([T[i] + o * 1000]), 5000)[0][0] for o in OFFS]
                erows.append((coin, W, name, T[i] // 1000, dt.datetime.fromtimestamp(T[i] / 1000, dt.timezone.utc).strftime('%d.%m %H:%M:%S'), round(float(np.expm1(mv[i])) * 100, 2), P[i], mkt, *[round(float(v), 10) if v == v else '' for v in vals]))
with open(outp + '-events.csv', 'w', newline='') as f: w = csv.writer(f); w.writerow(EH); w.writerows(erows)
print(coin, 'swaps', n, 'curve rows', len(curve), 'counts', len(counts))
