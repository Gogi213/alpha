#!/usr/bin/env python3
"""TK-401 П-29 (с правками Судьи R1–R7): отклик Gate на своп DEX ПО СОБЫТИЯМ, без сетки по времени и ffill дольше предела возраста A.
Запуск (calc, /data/tk0380/npvenv/bin/python): tk401-ev.py COIN MIDCSV OUTPREFIX [LEGCOIN]
MIDCSV = ts(с),block,logidx,price (цена исполнения свопа). Фьюч: gev2 /data/tk0402/gev2/COIN/*.npy (t мс, bid, ask, sz, sz) — состояние после каждого изменения лучшего уровня или его объёма.
Спот: последняя сделка <= t. Значение Gate на t годится, только если возраст последней записи <= A мс (A=5 с главное; 1, 30, inf — чувствительность) и час не дыра gev2 (нет файла / .n<100).
Событие: своп i, ход s*ln(p_i/p_j), j = последний своп с t_j <= t_i-W и t_j >= t_i-2W (иначе события нет); первое пересечение, следующее той же ячейки не раньше t_i+W.
Контроль (R2): случайные свопы той же монеты без события, с окном W, |ход|<X, знак s=знак своего хода, n = числу событий, сид фиксирован. Пред-ход Gate (R5): s*ln(G(t_i)/G(t_i-W)).
Выход: OUTPREFIX-curve.csv, OUTPREFIX-counts.csv, OUTPREFIX-manual.txt (3 события руками)."""
import sys, os, gzip, csv, glob, datetime as dt
import numpy as np
coin, midf, outp = sys.argv[1:4]
LEG = sys.argv[4] if len(sys.argv) > 4 else ''
D0 = (1792022400 - 3 * 86400) * 1000; ND = 8; DEND = D0 + ND * 86400000
HS = [1, 5, 30, 60, 90, 120] + list(range(150, 301, 30)) + list(range(360, 601, 60)) + list(range(900, 3601, 300)) + list(range(4500, 14401, 900))
assert len(HS) == 39
XS = [0.005, 0.01, 0.02, 0.05]; WS = [10, 60, 300]; AS = [1000, 5000, 30000, 10 ** 12]; ALAB = {1000: '1', 5000: '5', 30000: '30', 10 ** 12: 'inf'}
GEV = '/data/tk0402/gev2'; TR = '/srv/alpha-sda/tk372-gate/raw'
HDR = 'coin mkt A X W dir day h n_ev n_h dex_move_bp resp_bp ctrl_bp excess_bp share pre_bp'.split()
CH = 'coin mkt A X W dir n_swaps no_window n_cand n_event drop_hole drop_age n_ok n_ctrl_pool coin_days'.split()

sw = np.loadtxt(midf, delimiter=',', dtype=np.float64, ndmin=2) if midf.endswith('.csv') else None
if sw is None:
    rows = [l.split(',') for l in gzip.open(midf, 'rt')]
    sw = np.array([[float(x) for x in r] for r in rows])
sw = sw[(sw[:, 0] * 1000 >= D0) & (sw[:, 0] * 1000 < DEND)]
T = (sw[:, 0] * 1000).astype(np.int64); P = sw[:, 3]; BLK = sw[:, 1].astype(np.int64); LOGI = sw[:, 2].astype(np.int64); LP = np.log(P)
if LEG:
    sys.exit('нога-пересчёт (BNB/ETH) в пробе не нужна: берём монету с USD-квотой')

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
            G0, ok0, hole0, age0 = gval(g, T, A)
            Gp, okp, _, _ = gval(g, T - Wm, A)
            lG0 = np.log(G0); pre_all = np.log(G0) - np.log(Gp)
            # отклик на всех горизонтах для всех свопов — лениво, по подмножествам
            def resp(idx, s):
                out = np.full((len(idx), len(HS)), np.nan)
                for k, h in enumerate(HS):
                    G1, ok1, _, _ = gval(g, T[idx] + h * 1000, A)
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

# 3 события руками (фьюч, A=5 с, X=0,5 %, W=60 с): сырые строки gev2 до/после t_i и t_i+60 с — независимо от gval
if 'fut' in markets:
    gt, gm, hv = markets['fut']; W = 60; Wm = 60000
    j = np.searchsorted(T, T - Wm, side='right') - 1; hasj = (j >= 0) & (T[np.clip(j, 0, None)] >= T - 2 * Wm)
    mv = np.where(hasj, LP - LP[np.clip(j, 0, None)], np.nan); G0, ok0, _, _ = gval(markets['fut'], T, 5000)
    out = []
    for s in (1, -1):
        cand = np.flatnonzero(hasj & (s * mv >= np.log1p(0.005))); ev = []; last = -1
        for i in cand:
            if T[i] >= last: ev.append(i); last = T[i] + Wm
        ev = [i for i in ev if ok0[i]]
        pick = [ev[0], ev[len(ev) // 2], ev[-1]] if len(ev) >= 3 else ev
        for i in pick:
            tt = T[i]; t2 = tt + Wm; G1, ok1, _, _ = gval(markets['fut'], np.array([t2]), 5000)
            out.append(f'--- {coin} dir={"up" if s > 0 else "down"} своп i: ts={tt // 1000} (UTC {dt.datetime.fromtimestamp(tt / 1000, dt.timezone.utc):%d.%m %H:%M:%S}) block={BLK[i]} logidx={LOGI[i]} price={P[i]:.8g}; опорный j: ts={T[j[i]] // 1000} block={BLK[j[i]]} logidx={LOGI[j[i]]} price={P[j[i]]:.8g}; ход={s * mv[i] * 1e4:.1f} бп')
            for lab, tx in (('t_i', tt), ('t_i+60с', t2)):
                k = np.searchsorted(gt, tx, side='right') - 1
                out.append(f'  {lab}={tx}: gev2 строка до ({gt[k]}, mid={gm[k]:.8g}, возраст {tx - gt[k]} мс), после ({gt[k + 1] if k + 1 < len(gt) else None})')
            out.append(f'  скрипт: G0={G0[i]:.8g}, G(t+60)={G1[0]:.8g}, отклик={s * np.log(G1[0] / G0[i]) * 1e4:.2f} бп')
    open(outp + '-manual.txt', 'w').write('\n'.join(out) + '\n')
print(coin, 'swaps', n, 'curve rows', len(curve), 'counts', len(counts))
