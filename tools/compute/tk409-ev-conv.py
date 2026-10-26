#!/usr/bin/env python3
"""TK-409 П-30 §4 (с §11): схождение Gate-фьюча к DEX ПО СОБЫТИЯМ, форма (а) — первое пересечение, рыночный вход/выход по книге gev2.
Запуск (calc): GEV=/data/tk0393/tk401/gev2all /data/tk0380/npvenv/bin/python tk409-ev-conv.py COIN MIDCSV OUTPREFIX
MIDCSV = ts(с),block,logidx,price (как TK-401/407). Окно 15–22.10 (8 сут.). Событие = своп DEX или запись gev2; значение другой стороны — только в пределах возраста.
V1: A = живость потока gev2 (последняя любая запись <= A, час не дыра); цена DEX — своп не старше 60 с. V4: базис = медиана по ВРЕМЕНИ (сетка 10 с) за предыдущие 24 ч, обновляется раз в час
по завершённому; первые сутки без истории — вне. Отрыв dev = ln(Gate mid / цена DEX) - базис. Вход при первом |dev| >= T (до этого |dev| < T или состояние невалидно); позиция против отрыва,
рыночно на t+L по лучшему уровню (bid/ask) — спред вычтен ценой исполнения; qty лучшего < 1 лота -> drop_size. Выход: |dev| <= T*xf (xf = 0,5 или 0) на событии, иначе таймаут; рыночно на t_выхода+L.
Контроль: 5 случайных валидных событий той же монеты-суток с тем же знаком dev и той же длительностью удержания; край = эпизод - контроль.
Валовый ход = по ценам исполнения (спред внутри), до комиссий; нетто: тейкер VIP0 2*5 = 10 бп, со скидкой 2*3,25 = 6,5 бп; справочно 2*мейкер со скидкой 2,6 бп. Фандинг: флаг fund_cross (расчёт 00/08/16 UTC внутри удержания), сбой не нуль.
Выход: OUTPREFIX-cells.csv, OUTPREFIX-counts.csv, OUTPREFIX-eps.csv (главная клетка), OUTPREFIX-manual.txt."""
import sys, os, glob, datetime as dt
import numpy as np
coin, midf, outp = sys.argv[1:4]
D0 = 1792022400 * 1000; ND = 8; DEND = D0 + ND * 86400000; H = 3600000; DAY = 86400000
GEV = os.environ.get('GEV', '/data/tk0393/tk401/gev2all')
TRS = [0.01, 0.02, 0.03, 0.05]; XFS = [0.5, 0.0]; TOS = [300, 1800, 7200, 28800]
LS = [0, 100, 250, 500]; AS = [1000, 5000, 30000]; A0, L0 = 5000, 250
MAIN = (0.02, 0.5, 7200)
TAKER_V, TAKER_D, MAKER_D = 10.0, 6.5, 2.6

sw = np.loadtxt(midf, delimiter=',', dtype=np.float64, ndmin=2)
sw = sw[(sw[:, 0] * 1000 >= D0) & (sw[:, 0] * 1000 < DEND)]
sw = sw[np.argsort(sw[:, 0], kind='stable')]
ST = (sw[:, 0] * 1000).astype(np.int64); SP = sw[:, 3]; LSP = np.log(SP)

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
GT = a[:, 0].astype(np.int64); GB, GA, GBQ, GAQ = a[:, 1], a[:, 2], a[:, 3], a[:, 4]
GM = (GB + GA) / 2; GOK = np.isfinite(GM) & (GB < GA)

def gate_at(t, A):
    k = np.searchsorted(GT, t, side='right') - 1; kc = np.clip(k, 0, None)
    hi_t = np.clip((t - D0) // H, 0, ND * 24 - 1); hi_r = np.clip((GT[kc] - D0) // H, 0, ND * 24 - 1)
    ok = (k >= 0) & (t - GT[kc] <= A) & hv[hi_t] & hv[hi_r] & GOK[kc]
    return kc, ok
def dex_at(t):
    k = np.searchsorted(ST, t, side='right') - 1; kc = np.clip(k, 0, None)
    return kc, (k >= 0) & (t - ST[kc] <= 60000)

U = np.unique(np.concatenate([ST, GT[(GT >= D0) & (GT < DEND)]]))
kd, okd = dex_at(U); LP_U = LSP[kd]
GRID = np.arange(D0, DEND, 10000, dtype=np.int64); kgd, okgd = dex_at(GRID)
SER = {}
for A in AS:
    kg, okg = gate_at(U, A); lg = np.log(np.where(okg, GM[kg], np.nan))
    d_raw = lg - LP_U; ok = okg & okd
    kg2, okg2 = gate_at(GRID, A); dg = np.log(np.where(okg2, GM[kg2], np.nan)) - LSP[kgd]; dg[~(okg2 & okgd)] = np.nan
    bh = np.full(ND * 24, np.nan); nhist = 0
    for h in range(24, ND * 24):  # окно [h-24ч, h) по сетке 10 с; нужно >= 50 % валидных (V4)
        w = dg[(h - 24) * 360:h * 360]; w = w[np.isfinite(w)]
        if len(w) >= 4320: bh[h] = np.median(w)
    b = bh[np.clip((U - D0) // H, 0, ND * 24 - 1)]
    dev = d_raw - b; val = ok & np.isfinite(dev)
    SER[A] = dict(dev=np.where(val, dev, 0.0), val=val, kg=kg, raw=d_raw, lg=lg, nb=int(np.isfinite(bh).sum()))
dayok = [int(hv[d * 24:(d + 1) * 24].sum()) >= 12 for d in range(ND)]  # V7: годные сутки (>= 12 часов вне дыр)
SETTLE = np.array([D0 + d * DAY + k * 8 * H for d in range(ND + 1) for k in range(3)], np.int64)

def px(t, A, q, open_):
    """цена исполнения рыночного хода q (+1 купить / -1 продать) на t: (цена, статус) статус 0 ок, 1 не жив, 2 qty<1"""
    k, ok = gate_at(np.array([t]), A); k = int(k[0])
    if not ok[0]: return np.nan, 1, k
    p = GA[k] if q > 0 else GB[k]; qty = GAQ[k] if q > 0 else GBQ[k]
    return p, (0 if qty >= 1 else 2), k

def run(Tr, xf, TO, L, A, want_eps=False):
    S = SER[A]; dev = S['dev']; val = S['val']; lg = S['lg']
    ab = val & (np.abs(dev) >= Tr); prev = np.r_[True, ~ab[:-1]]
    cand = np.nonzero(ab & prev)[0]
    rng = np.random.default_rng([409, int(Tr * 1e4), int(xf * 10), TO, L, A])
    eps = []; cnt = dict(n_cand=len(cand), drop_hist=0, drop_age=0, drop_size=0, drop_exit_age=0, exit_thin=0, busy=0)
    last = -1; vidx_all = np.nonzero(val)[0]; dayU = (U - D0) // DAY
    for i in cand:
        if U[i] < D0 + DAY: cnt['drop_hist'] += 1; continue  # ровно сутки без истории базиса
        if i <= last: cnt['busy'] += 1; continue
        s = 1 if dev[i] > 0 else -1; q = -s
        pe, st, ke = px(U[i] + L, A, q, True)
        if st == 1: cnt['drop_age'] += 1; continue
        if st == 2: cnt['drop_size'] += 1; continue
        jmax = int(np.searchsorted(U, U[i] + TO, side='right')); j0 = max(i + 1, int(np.searchsorted(U, U[i] + L, side='left')))
        seg = slice(j0, jmax); hit = np.nonzero(val[seg] & (s * dev[seg] <= Tr * xf))[0]
        if len(hit): jx = j0 + int(hit[0]); tx = int(U[jx]); kind = 'exit'
        else: jx = jmax - 1; tx = int(U[i] + TO); kind = 'timeout'
        px_, st2, kx = px(tx + L, A, -q, False)
        if st2 == 1: cnt['drop_exit_age'] += 1; continue
        if st2 == 2: cnt['exit_thin'] += 1
        last = jx
        g = q * (np.log(px_) - np.log(pe)) * 1e4
        # разложение: ход Gate (mid) и DEX (цена) в сторону схождения, бп
        gx = np.log(GM[kx]); gi = lg[i] if np.isfinite(lg[i]) else np.nan
        kdx, okx = dex_at(np.array([tx])); lpx = LSP[int(kdx[0])]
        g_mv = -s * (gx - gi) * 1e4; d_mv = s * (lpx - LP_U[i]) * 1e4
        fund = bool(((SETTLE > U[i] + L) & (SETTLE <= tx + L)).any())
        # контроль: 5 случайных валидных событий той же монеты-суток, тот же знак dev, то же удержание
        pool = vidx_all[(dayU[vidx_all] == dayU[i]) & (np.sign(dev[vidx_all]) == s)]
        cg = []
        for r in (rng.choice(pool, 5) if len(pool) else []):
            p1, s1, _ = px(U[r] + L, A, q, True); p2, s2, _ = px(U[r] + (tx - U[i]) + L, A, -q, False)
            if s1 == 0 and s2 != 1 and np.isfinite(p2): cg.append(q * (np.log(p2) - np.log(p1)) * 1e4)
        eps.append((int(U[i]), int(dayU[i]), s, g, float(np.mean(cg)) if cg else np.nan, kind, tx - int(U[i]), g_mv, d_mv, fund, int(i), jx, ke, kx))
    return eps, cnt

def summ(eps):
    if not eps: return None
    g = np.array([e[3] for e in eps]); c = np.array([e[4] for e in eps]); day = np.array([e[1] for e in eps]); ex = g - c
    okc = np.isfinite(c); days = sorted(set(day.tolist()))
    pd = {d: (int((day == d).sum()), float(g[day == d].mean())) for d in days}
    rng = np.random.default_rng(409); bs = []
    for _ in range(2000):
        pick = rng.choice(days, len(days)); idx = np.concatenate([np.nonzero(day == d)[0] for d in pick]); bs.append(np.nanmean(ex[idx]))
    good = [d for d in days if dayok[d]]; pos = sum(1 for d in good if pd[d][1] > 0)
    return dict(n=len(g), gross=g.mean(), med=float(np.median(g)), ctrl=float(np.nanmean(c)) if okc.any() else np.nan, excess=float(np.nanmean(ex)), ci_lo=float(np.percentile(bs, 2.5)), ci_hi=float(np.percentile(bs, 97.5)),
                net_v=g.mean() - TAKER_V, net_d=g.mean() - TAKER_D, ref_mk=g.mean() - MAKER_D, hit=float((g > 0).mean()), n_days=len(days), good_days=len(good), pos_good=pos,
                gate_mv=float(np.mean([e[7] for e in eps])), dex_mv=float(np.mean([e[8] for e in eps])), n_fund=int(sum(e[9] for e in eps)), per_day=';'.join(f'{d}:{pd[d][0]}:{pd[d][1]:.1f}' for d in days))

rows = []; crow = []
def do(Tr, xf, TO, L, A, tag):
    eps, cnt = run(Tr, xf, TO, L, A); s = summ(eps)
    crow.append(dict(tag=tag, T=Tr, xf=xf, TO=TO, L=L, A=A, **cnt))
    if s: rows.append(dict(tag=tag, T=Tr, xf=xf, TO=TO, L=L, A=A, **s))
    return eps
for Tr in TRS:
    for xf in XFS:
        for TO in TOS: do(Tr, xf, TO, L0, A0, 'grid')
for L in LS:
    for A in AS:
        if (L, A) != (L0, A0): do(MAIN[0], MAIN[1], MAIN[2], L, A, 'sens')
main = do(*MAIN, L0, A0, 'main')
import csv
def wr(fn, rs):
    if not rs: return
    with open(fn, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rs[0].keys())); w.writeheader(); w.writerows(rs)
wr(outp + '-cells.csv', [dict(coin=coin, **r) for r in rows]); wr(outp + '-counts.csv', [dict(coin=coin, nbasis_hours=SER[A0]['nb'], good_days=sum(dayok), **r) for r in crow])
wr(outp + '-eps.csv', [dict(coin=coin, t_ms=e[0], day=e[1], s=e[2], gross_bp=e[3], ctrl_bp=e[4], kind=e[5], hold_ms=e[6], gate_mv_bp=e[7], dex_mv_bp=e[8], fund_cross=e[9]) for e in main])
with open(outp + '-manual.txt', 'w') as f:
    f.write(f'{coin}: 3 эпизода главной клетки T={MAIN[0]} xf={MAIN[1]} TO={MAIN[2]} L={L0} A={A0}\n')
    for e in ([main[0], main[len(main) // 2], main[-1]] if len(main) >= 3 else main):
        i, jx = e[10], e[11]; kd_ = int(np.searchsorted(ST, U[i], side='right') - 1)
        f.write(f'\n--- вход сигнал t={U[i]} ({dt.datetime.fromtimestamp(U[i] / 1000, dt.timezone.utc):%d.%m %H:%M:%S.%f}) s={e[2]} -> позиция {"long" if e[2] < 0 else "short"} Gate\n')
        f.write(f'своп: ts_с={sw[kd_,0]:.0f} блок={sw[kd_,1]:.0f} logidx={sw[kd_,2]:.0f} цена={SP[kd_]:.10g}; возраст свопа {U[i]-ST[kd_]} мс\n')
        f.write(f'gate на t: {GT[e[12]]} bid={GB[e[12]]} ask={GA[e[12]]} bq={GBQ[e[12]]} aq={GAQ[e[12]]} (это строка входа при t+L)\n')
        f.write(f'dev сигнала {SER[A0]["dev"][i]*1e4:.1f} бп (сырой {SER[A0]["raw"][i]*1e4:.1f} бп); выход {e[5]} через {e[6]} мс; строка выхода {GT[e[13]]} bid={GB[e[13]]} ask={GA[e[13]]}\n')
        f.write(f'валовый {e[3]:.2f} бп (спред внутри), контроль {e[4]:.2f}, ход Gate {e[7]:.1f}, ход DEX {e[8]:.1f}, fund_cross={e[9]}\n')
print(coin, 'ok', len(main), 'эп. главной клетки; сутки годные', sum(dayok))
