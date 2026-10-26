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
D0 = int(os.environ.get('D0S', 1792022400)) * 1000; ND = int(os.environ.get('ND', 8)); DEND = D0 + ND * 86400000; H = 3600000; DAY = 86400000
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
    NB = int(max(1, min(20, len(anch) // 40))); edges = np.percentile(ax, np.linspace(0, 100, NB + 1)); edges[-1] += 1e-9  # v2: число бинов по числу якорей (на длинных W 20 бинов давали <5 в ячейке)
    for h in sorted(set(HFIX + [W])):
        mm, ex, ok = resp(t0, h, s)
        cm, cx, cok = resp(t0[qa], h, s[qa]); cday = day[qa]
        c[f'ok_h{h}'] = int(ok.sum())
        cmean_x = {d: (np.nanmean(cx[cday == d]) if np.isfinite(cx[cday == d]).any() else np.nan) for d in range(ND)}
        cmean_m = {d: (np.nanmean(cm[cday == d]) if np.isfinite(cm[cday == d]).any() else np.nan) for d in range(ND)}
        for b in range(NB):
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
    bi = np.nonzero(ax >= 500)[0]
    if len(bi):  # >= 5 %: путь Gate до/после (по событиям, A=5 с), время до 50/90 % догона (первое прохождение на сетке смещений)
        OF = [-W, -W // 2, 0, 1, 2, 5, 10, 30, 60, 120, 300, 600, 1800, 3600, 7200, 14400, 28800, 86400]
        tvb = TV[anch][bi]; g0 = g_tv[bi]; sb = s[bi]; xb = ax[bi] / 1e4
        pth = {}
        for o in OF:
            pth[o] = gmid(tvb + o * 1000, A_IN)[0] - g0
        post = [o for o in OF if o >= 1]
        for n, i in enumerate(bi):
            sh = [(sb[n] * pth[o][n] / xb[n]) if np.isfinite(pth[o][n]) else np.nan for o in post]
            lastobs = max([o for o in post if tvb[n] + o * 1000 < DEND] or [0])
            def first(th):
                for o, v in zip(post, sh):
                    if np.isfinite(v) and v >= th: return o
                return np.nan
            big.append(dict(coin=coin, W=W, ts=int(TS[anch[i]]), X_bp=float(ax[i] * s[i]), strat=int(strat[i]), t50=first(0.5), t90=first(0.9), last_obs=lastobs,
                            **{f'G{o}': (float(pth[o][n]) if np.isfinite(pth[o][n]) else np.nan) for o in OF}))
    cnt.append(c)
def wr(fn, rs):
    if not rs: return
    keys = list(dict.fromkeys(k for r in rs for k in r))
    with open(fn, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=keys); w.writeheader(); w.writerows(rs)
wr(outp + '-curve.csv', rows); wr(outp + '-counts.csv', cnt); wr(outp + '-big.csv', big)

# ---- v2: В2 время схождения / В3 кто к кому / В4 признаки денег (П-31 §4-6). Пул-события: TV = tv, значения Gate на tv+L.
te = TV + L; lg, okg = gmid(te, A_IN); draw = lg - LM          # d = ln G - ln DEX (сырой), только где Gate жив (A=5 с)
wgt = np.minimum(np.diff(te, append=te[-1]), 3600000).astype(float)
basis = np.full(len(te), np.nan)
for hh in range(24, ND * 24):                                    # базис: медиана d за предыдущие 24 ч, по длительности, обновление раз в час, без заглядывания
    t1 = D0 + hh * H; i1 = np.searchsorted(te, t1); i0 = np.searchsorted(te, t1 - DAY)
    mk = okg[i0:i1]
    if mk.sum() < 20: continue
    v = draw[i0:i1][mk]; w = wgt[i0:i1][mk]; o = np.argsort(v); cw = np.cumsum(w[o]); bval = v[o][np.searchsorted(cw, cw[-1] / 2)]
    j1 = np.searchsorted(te, t1 + H); basis[i1:j1] = bval
dev = draw - basis                                               # NaN: нет живости или нет истории базиса (первые сутки — вне)
conv = []; DMIN = 0.003
cand = np.nonzero(np.abs(dev) >= DMIN)[0]; nxt = 0
def first_hit(mask):
    return int(np.argmax(mask)) if mask.any() else -1
for c0 in cand:
    t0 = te[c0]
    if t0 < nxt: continue
    d0 = dev[c0]; sg = np.sign(d0); ad = abs(d0); lg0 = lg[c0]; lm0 = LM[c0]; bs0 = basis[c0]
    tend = min(t0 + DAY, DEND); j = np.searchsorted(te, tend)
    # пул-сторона: схождение и 25 % хода DEX к Gate
    dv = dev[c0 + 1:j]; pc = first_hit(np.abs(dv) <= ad / 2)
    tp_conv = te[c0 + 1 + pc] if pc >= 0 else np.nan
    lmv = LM[c0 + 1:j]; p25 = first_hit(sg * (lmv - lm0) >= 0.25 * ad); tp25 = te[c0 + 1 + p25] if p25 >= 0 else np.nan
    # Gate-сторона по событиям Gate, окнами 30 с/5 мин/1 ч/сутки
    tg_conv = np.nan; tg25 = np.nan; gl = np.searchsorted(GT, t0, side='right'); gend = np.searchsorted(GT, tend, side='right')
    cv_src = 'pool' if pc >= 0 else ''; gc_mid = np.nan; gc_lm = np.nan
    for e in (30000, 300000, 3600000, DAY):
        gh = min(gend, np.searchsorted(GT, t0 + e, side='right'))
        if gh > gl:
            tt = GT[gl:gh]; hi_ = np.clip((tt - D0) // H, 0, ND * 24 - 1)
            ok = GOK[gl:gh] & hv[hi_]
            gm = np.log((GB[gl:gh] + GA[gl:gh]) / 2)
            lmat = LM[np.clip(np.searchsorted(TV + L, tt, side='right') - 1, 0, None)]
            dg = gm - lmat - bs0
            if np.isnan(tg_conv):
                k = first_hit(ok & (np.abs(dg) <= ad / 2))
                if k >= 0: tg_conv = tt[k]; gc_mid = gm[k]; gc_lm = lmat[k]
            if np.isnan(tg25):
                k = first_hit(ok & (-sg * (gm - lg0) >= 0.25 * ad))
                if k >= 0: tg25 = tt[k]
            gl = gh
        if (not np.isnan(tg_conv) and not np.isnan(tg25)) or gl >= gend: break
    tc = np.nanmin([tp_conv, tg_conv]) if not (np.isnan(tp_conv) and np.isnan(tg_conv)) else np.nan
    if not np.isnan(tc):
        if tc == tp_conv: g_c = lg[c0 + 1 + pc]; lm_c = LM[c0 + 1 + pc]; cv_src = 'pool'
        else: g_c = gc_mid; lm_c = gc_lm; cv_src = 'gate'
        dG = g_c - lg0; dD = lm_c - lm0; red_g = -sg * dG; red_d = sg * dD
        shg = red_g / (red_g + red_d) if (red_g + red_d) > 0 else np.nan
    else: dG = dD = shg = np.nan
    first_mv = 'none' if np.isnan(tp25) and np.isnan(tg25) else ('gate' if (np.isnan(tp25) or tg25 < tp25) else ('pool' if (np.isnan(tg25) or tp25 < tg25) else 'same'))
    conv.append(dict(coin=coin, t0=int(t0 // 1000), d0_bp=float(d0 * 1e4), tconv_s=(float((tc - t0) / 1000) if not np.isnan(tc) else np.nan), obs_s=float((tend - t0) / 1000),
                     conv_src=cv_src if not np.isnan(tc) else '', dG_bp=float(dG * 1e4), dD_bp=float(dD * 1e4), share_gate=float(shg),
                     tg25_s=(float((tg25 - t0) / 1000) if not np.isnan(tg25) else np.nan), tp25_s=(float((tp25 - t0) / 1000) if not np.isnan(tp25) else np.nan), first=first_mv))
    nxt = tc if not np.isnan(tc) else tend
wr(outp + '-conv.csv', conv)
# В3 опережение: корреляция доходностей DEX (последний видимый своп) и Gate-середины на лагах tau, шаг сетки = w (читается из событий)
xc = []; GDAYS = np.array(dayok)
for w in (10, 60, 300):
    tg = np.arange(D0 + 24 * H, DEND, w * 1000)
    lmat = lambda t: LM[np.clip(np.searchsorted(TV, t, side='right') - 1, 0, None)] + np.where(np.searchsorted(TV, t, side='right') > 0, 0, np.nan)
    rD = lmat(tg) - lmat(tg - w * 1000)
    for tau in (-300, -60, -10, 0, 10, 60, 300):
        g1, o1 = gmid(tg + tau * 1000, A_IN); g0_, o0 = gmid(tg + tau * 1000 - w * 1000, A_IN)
        rG = g1 - g0_; m = o1 & o0 & np.isfinite(rD) & np.isfinite(rG) & GDAYS[np.clip((tg - D0) // DAY, 0, ND - 1)]
        if m.sum() < 30 or rD[m].std() == 0 or rG[m].std() == 0: xc.append(dict(coin=coin, w=w, tau=tau, n=int(m.sum()), corr=np.nan, nzD=int((rD[m] != 0).sum()) if m.any() else 0)); continue
        xc.append(dict(coin=coin, w=w, tau=tau, n=int(m.sum()), corr=float(np.corrcoef(rD[m], rG[m])[0, 1]), nzD=int((rD[m] != 0).sum())))
wr(outp + '-xc.csv', xc)
# В4 признаки: глубина Gate (медиана по gev2 (bq*bid + aq*ask)/2, единицы количества как в gev2), число своп-событий, события Gate
dep = np.nanmedian((a[:, 3] * a[:, 1] + a[:, 4] * a[:, 2]) / 2)
wr(outp + '-feat.csv', [dict(coin=coin, gate_depth_med=float(dep), pool_events=int(len(TS)), gate_events=int(len(GT)), good_days=int(sum(dayok)), episodes_conv=len(conv))])
with open(outp + '-w9.txt', 'w') as f:
    f.write(f'{coin} W9: {w9}\nмид-источник: пул (v3 sqrtPriceX96), доля не-пулового 0\nгодные сутки gev2 (>=12 ч): {sum(dayok)}/{ND}, часов с данными {int(hv.sum())}/{ND*24}\n')
print(coin, 'ok', len(rows), 'строк кривой', len(big), 'больших ходов')
