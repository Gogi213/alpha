#!/usr/bin/env python3
"""TK-410 П-31 §2-3 (проба): вопрос 1 — отклик Gate-фьюча на ход DEX, ПО СОБЫТИЯМ. Главная метрика П-31 W2: исполнимый избыток
нетто (касание, -2,6 бп) при h=W, страта P<25 % X, середина пула (W8), вход A=5 с, V=1000, L=250.
Запуск (calc): GEV=/data/tk0393/tk401/gev2all /data/tk0380/npvenv/bin/python tk410-map.py COIN RAWCSV OUTPREFIX
RAWCSV = выход tk410-extract.py (ts,block,logidx,kind,mid_raw,liq0,liq1,tick). X, R — логарифмы отношений (масштаб десятичных не нужен).
Якорь: своп i, j = последний своп с ts_j in [ts_i-2W, ts_i-W]; жадно без перекрытия (следующий якорь >= ts_i+W). Контроль: якоря той же
монеты-суток с |X| <= нижнего квартиля |X| (монета, W), знак — собственного хода; тот же R_h.
Выход: OUTPREFIX-curve.csv (20 интервалов |X| x W x h x направление), -counts.csv, -big.csv (ходы >= 5 %), -w9.txt."""
import sys, os, glob, csv, datetime as dt
import numpy as np
coin, rawf, outp = sys.argv[1:4]
D0 = 1792022400 * 1000; ND = 8; DEND = D0 + ND * 86400000; H = 3600000; DAY = 86400000
GEV = os.environ.get('GEV', '/data/tk0393/tk401/gev2all')
VIS, L, A_IN, NET = 1000, 250, 5000, 2.6
WS = [1, 5, 10, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 14400, 28800, 43200, 86400]
HFIX = [1, 10, 60, 300, 3600]
raw = np.genfromtxt(rawf, delimiter=',', names=True, dtype=None, encoding=None)
w9 = dict(rows=len(raw), kinds={str(k): int((raw['kind'] == k).sum()) for k in set(raw['kind'])},
          liq0_nonnull=int(sum(1 for x in raw['liq0'] if str(x) not in ('', 'nan'))),
          liq1_nonnull=int(sum(1 for x in raw['liq1'] if str(x) not in ('', 'nan'))))
_, ui = np.unique(np.stack([raw['block'], raw['logidx']], 1), axis=0, return_index=True)
raw = raw[np.sort(ui)]; raw = raw[np.argsort(raw['ts'], kind='stable')]
TS = raw['ts'].astype(np.int64); LM = np.log(raw['mid_raw'].astype(np.float64))
m_in = (TS * 1000 >= D0) & (TS * 1000 < DEND)
TS, LM = TS[m_in], LM[m_in]; TV = TS * 1000 + VIS

hv = np.zeros(ND * 24 + 2, bool); parts = []
for f in sorted(glob.glob(f'{GEV}/{coin}/??????????.npy')):
    try: n = int(open(f[:-4] + '.n').read())
    except OSError: n = -1
    if n < 100: continue
    d = dt.datetime.strptime(os.path.basename(f)[:-4], '%Y%m%d%H').replace(tzinfo=dt.timezone.utc)
    hi = int(d.timestamp()) // 3600 - D0 // H
    if 0 <= hi < ND * 24: hv[hi] = True; parts.append(np.load(f))
if not parts: sys.exit('SKIP: нет gev2')
a = np.concatenate(parts); a = a[np.argsort(a[:, 0], kind='stable')]
GT = a[:, 0].astype(np.int64); GB, GA = a[:, 1], a[:, 2]; GOK = np.isfinite(GB) & np.isfinite(GA) & (GB < GA)
dayok = [int(hv[d * 24:(d + 1) * 24].sum()) >= 12 for d in range(ND)]

def gate_at(t, A):
    k = np.searchsorted(GT, t, side='right') - 1; kc = np.clip(k, 0, None)
    hi_t = np.clip((t - D0) // H, 0, ND * 24 - 1); hi_r = np.clip((GT[kc] - D0) // H, 0, ND * 24 - 1)
    return kc, (k >= 0) & (t - GT[kc] <= A) & hv[hi_t] & hv[hi_r] & GOK[kc]
def gmid(t, A):
    k, ok = gate_at(t, A); return np.where(ok, np.log((GB[k] + GA[k]) / 2), np.nan), ok
def resp(t0, h, s):
    """R_h: mid-to-mid и исполнимый (касание) в сторону s, бп; вход A=5 с, выход A=5 с (h<300) или 60 с"""
    Ax = A_IN if h < 300 else 60000
    k0, o0 = gate_at(t0, A_IN); k1, o1 = gate_at(t0 + h * 1000, Ax)
    ok = o0 & o1 & (t0 + h * 1000 < DEND)
    mm = s * (np.log((GB[k1] + GA[k1]) / 2) - np.log((GB[k0] + GA[k0]) / 2)) * 1e4
    pin = np.where(s > 0, GA[k0], GB[k0]); pout = np.where(s > 0, GB[k1], GA[k1])
    ex = s * (np.log(pout) - np.log(pin)) * 1e4
    return np.where(ok, mm, np.nan), np.where(ok, ex, np.nan), ok

cnt = []; rows = []; big = []; rng = np.random.default_rng(410)
for W in WS:
    jlo = np.searchsorted(TS, TS - 2 * W, side='left'); jhi = np.searchsorted(TS, TS - W, side='right') - 1
    has = (jhi >= jlo) & (jhi >= 0)
    X = np.where(has, LM - LM[np.clip(jhi, 0, None)], np.nan)
    anch = []; nxt = -1
    for i in np.nonzero(has & (np.abs(X) > 0))[0]:
        if TS[i] >= nxt: anch.append(i); nxt = TS[i] + W
    anch = np.array(anch, int); c = dict(coin=coin, W=W, swaps=len(TS), with_prev=int(has.sum()), anchors=len(anch))
    if len(anch) < 10: cnt.append(c); continue
    s = np.sign(X[anch]).astype(float); ax = np.abs(X[anch]) * 1e4; t0 = TV[anch] + L; day = (TV[anch] - D0) // DAY
    # пред-ход Gate P = s*ln(G(tv)/G(ts-W)) (A=5 с на обеих точках)
    g_tv, o1 = gmid(TV[anch], A_IN); g_pw, o2 = gmid(TS[anch] * 1000 - W * 1000, A_IN)
    P = s * (g_tv - g_pw) * 1e4; okP = o1 & o2
    strat = np.where(~okP, 2, np.where(P < 0.25 * ax, 0, 1))   # 0 = Gate ещё не ушёл (главная), 1 = ушёл, 2 = нет живости
    c.update(strat0=int((strat == 0).sum()), strat1=int((strat == 1).sum()), drop_age_pre=int((strat == 2).sum()))
    q1 = np.percentile(ax, 25); qa = np.nonzero(ax <= q1)[0]
    edges = np.percentile(ax, np.linspace(0, 100, 21)); edges[-1] += 1e-9
    for h in sorted(set(HFIX + [W])):
        mm, ex, ok = resp(t0, h, s)
        cm, cx, cok = resp(t0[qa], h, s[qa]); cday = day[qa]
        c[f'ok_h{h}'] = int(ok.sum())
        cmean_x = {d: (np.nanmean(cx[cday == d]) if np.isfinite(cx[cday == d]).any() else np.nan) for d in range(ND)}
        cmean_m = {d: (np.nanmean(cm[cday == d]) if np.isfinite(cm[cday == d]).any() else np.nan) for d in range(ND)}
        for b in range(20):
            for sg in (0, 1):
                m = (ax >= edges[b]) & (ax < edges[b + 1]) & (strat == 0) & ok & (s == (1 if sg else -1))
                if m.sum() < 5: continue
                ev_days = day[m]
                exc_x = ex[m] - np.array([cmean_x[d] for d in ev_days]); exc_m = mm[m] - np.array([cmean_m[d] for d in ev_days])
                dd = sorted(set(ev_days.tolist())); bs = []
                for _ in range(500):
                    pk = rng.choice(dd, len(dd)); idx = np.concatenate([np.nonzero(ev_days == d)[0] for d in pk]); bs.append(np.nanmean(exc_x[idx]))
                rows.append(dict(coin=coin, W=W, h=h, dir='up' if sg else 'down', bin=b, n=int(m.sum()), n_days=len(dd), X_lo=edges[b], X_hi=edges[b + 1], X_mean=float(ax[m].mean()),
                                 R_mid=float(np.nanmean(mm[m])), R_exec=float(np.nanmean(ex[m])), exc_mid=float(np.nanmean(exc_m)), exc_exec=float(np.nanmean(exc_x)),
                                 net=float(np.nanmean(exc_x)) - NET, ci_lo=float(np.percentile(bs, 2.5)), ci_hi=float(np.percentile(bs, 97.5)),
                                 share=float(np.nanmean(exc_x) / ax[m].mean()), pos_days=int(sum(1 for d in dd if np.nanmean(exc_x[ev_days == d]) > 0))))
    for i, k in enumerate(anch):
        if ax[i] >= 500:  # >= 5 %: список эпизодов, путь Gate по событиям (A=5 с; нет значения — NaN)
            ofs = [-W, 0, 1, 10, 60, 300, 1800]; gv = [gmid(np.array([TV[k] + o * 1000]), A_IN)[0][0] for o in ofs]
            big.append(dict(coin=coin, W=W, ts=int(TS[k]), X_bp=float(ax[i] * s[i]), strat=int(strat[i]),
                            **{f'G{o}': (float((gv[n] - g_tv[i]) * 1e4) if np.isfinite(gv[n]) and np.isfinite(g_tv[i]) else np.nan) for n, o in enumerate(ofs)}))
    cnt.append(c)
def wr(fn, rs):
    if not rs: return
    keys = list(dict.fromkeys(k for r in rs for k in r))
    with open(fn, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=keys); w.writeheader(); w.writerows(rs)
wr(outp + '-curve.csv', rows); wr(outp + '-counts.csv', cnt); wr(outp + '-big.csv', big)
with open(outp + '-w9.txt', 'w') as f:
    f.write(f'{coin} W9: {w9}\nмид-источник: пул (v3 sqrtPriceX96), доля не-пулового 0\nгодные сутки gev2 (>=12 ч): {sum(dayok)}/{ND}, часов с данными {int(hv.sum())}/{ND*24}\n')
print(coin, 'ok', len(rows), 'строк кривой', len(big), 'больших ходов')
