#!/usr/bin/env python3
"""TK-409 П-30 §5 (с §11): «ёршик» Y ПО СОБЫТИЯМ — двусторонние мейкер-котировки Gate-фьюча вокруг линии DEX, очередь по сделкам мс (пессимистично, V2).
Запуск (calc): GEV=/data/tk0393/tk401/gev2all /data/tk0380/npvenv/bin/python tk409-ev-yorsh.py COIN MIDCSV OUTPREFIX [VIS_MS]
Линия = цена DEX (своп, виден с tv = ts*1000 + VIS; главная 1000, чувств. 2000/3000) * exp(базис) (базис — медиана по времени за пред. 24 ч, как S, V4).
Котировки bid = линия*(1-k), ask = линия*(1+k) (на тик внутрь не округляем: bid вниз, ask вверх), пересчёт на каждом событии DEX не чаще 1 раза в 250 мс, действуют с tv+L (L=250), заменяются следующей;
котировка живёт не дольше 60 с без нового свопа. Нужна живость gev2 (A=5 с) на моменте активации; bid >= ask книги -> post-only отказ (счётчик).
Очередь (пессимистично, отмены впереди не считаем): цена внутри спреда -> впереди 0; на лучшем уровне -> впереди bq/aq на активации; глубже лучшего -> только сделка СКВОЗЬ цену.
Заполнение bid: тейкер-продажа (size<0) по цене < bid — сразу; по цене == bid — когда накоплено >= впереди + 1 лот. Ask — зеркально (size>0).
Позиция +-1: вход = первое заполнение любой стороны, встречная сторона стоит как есть (с последующими пересчётами линии), выход = её заполнение, иначе по таймауту T рыночно на (вход+T+L) по книге (qty < 1 лота -> exit_thin).
Контроль: линия DEX сдвинута на +3 ч (отрицательный контроль П-30 §5); край = основной - контроль, интервал блочным бутстрапом по суткам.
Валовый ход = по ценам заполнения/исполнения, до комиссий. Справочно: 2*мейкер со скидкой 2,6; VIP0 мейкер 2/тейкер 5 (круг мейкер+мейкер 4, мейкер+тейкер 7).
Выход: OUTPREFIX-cells.csv, OUTPREFIX-counts.csv, OUTPREFIX-eps.csv (главная k=0,2 % T=300), OUTPREFIX-manual.txt."""
import sys, os, glob, gzip, datetime as dt
import numpy as np
coin, midf, outp = sys.argv[1:4]
VIS = int(sys.argv[4]) if len(sys.argv) > 4 else 1000
D0 = 1792022400 * 1000; ND = 8; DEND = D0 + ND * 86400000; H = 3600000; DAY = 86400000
GEV = os.environ.get('GEV', '/data/tk0393/tk401/gev2all'); TRD = os.environ.get('GTR', '/srv/alpha-sda/tk372-gate/raw/fut')
KS = [0.002, 0.005, 0.01]; TS = [300, 1800]; L = 250; A = 5000; THR = 250; SHIFT3H = 3 * H
MAIN = (0.002, 300)

sw = np.loadtxt(midf, delimiter=',', dtype=np.float64, ndmin=2)
sw = sw[(sw[:, 0] * 1000 >= D0) & (sw[:, 0] * 1000 < DEND)]
sw = sw[np.argsort(sw[:, 0], kind='stable')]
ST0 = (sw[:, 0] * 1000).astype(np.int64) + VIS; SP = sw[:, 3]; LSP = np.log(SP)

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
ub = np.unique(GB[np.isfinite(GB)][:300000]); TICK = float(np.min(np.diff(ub))) if len(ub) > 1 else 0.0
if TICK <= 0: sys.exit('SKIP: тик не определён')

tp = []
for d in range(ND):
    day = (dt.datetime(2026, 10, 15, tzinfo=dt.timezone.utc) + dt.timedelta(days=d)).strftime('%Y-%m-%d')
    fn = f'{TRD}/{coin}_USDT/{day}.csv.gz'
    if not os.path.exists(fn): continue
    x = np.loadtxt(fn, delimiter=',', skiprows=1, usecols=(3, 4, 5), dtype=np.float64, ndmin=2)
    if len(x): tp.append(x)
if not tp: sys.exit('SKIP: нет сделок')
tr = np.concatenate(tp); tr = tr[np.argsort(tr[:, 0], kind='stable')]
TT = np.round(tr[:, 0] * 1000).astype(np.int64); TSZ = tr[:, 1]; TPX = tr[:, 2]

def gate_at(t):
    k = int(np.searchsorted(GT, t, side='right')) - 1
    if k < 0: return -1
    hi_t = min(max((t - D0) // H, 0), ND * 24 - 1); hi_r = min(max((GT[k] - D0) // H, 0), ND * 24 - 1)
    return k if (t - GT[k] <= A and hv[hi_t] and hv[hi_r] and GOK[k]) else -1

def dex_idx(ST, t):
    k = np.searchsorted(ST, t, side='right') - 1; kc = np.clip(k, 0, None)
    return kc, (k >= 0) & (t - ST[kc] <= 60000)

# базис: окно 24 ч по сетке 10 с, нужно >= 50 % валидных (как в S, V4)
GRID = np.arange(D0, DEND, 10000, dtype=np.int64)
kg = np.searchsorted(GT, GRID, side='right') - 1; kgc = np.clip(kg, 0, None)
hg = np.clip((GRID - D0) // H, 0, ND * 24 - 1); hgr = np.clip((GT[kgc] - D0) // H, 0, ND * 24 - 1)
okg = (kg >= 0) & (GRID - GT[kgc] <= A) & hv[hg] & hv[hgr] & GOK[kgc]
def basis(ST):
    kd, okd = dex_idx(ST, GRID); dg = np.log(np.where(okg, GM[kgc], np.nan)) - LSP[kd]; dg[~(okg & okd)] = np.nan
    bh = np.full(ND * 24, np.nan)
    for h in range(24, ND * 24):
        w = dg[(h - 24) * 360:h * 360]; w = w[np.isfinite(w)]
        if len(w) >= 4320: bh[h] = np.median(w)
    return bh
dayok = [int(hv[d * 24:(d + 1) * 24].sum()) >= 12 for d in range(ND)]
SETTLE = np.array([D0 + d * DAY + k * 8 * H for d in range(ND + 1) for k in range(3)], np.int64)

def intervals(ST, bh):
    """котировочные интервалы: (act, end, линия=цена*exp(базис), tv свопа)"""
    out = []; last = -10**18
    for i in range(len(ST)):
        tv = int(ST[i])
        if tv < D0 + DAY or tv >= DEND: continue  # первые сутки без истории базиса — вне
        if tv - last < THR: continue
        b = bh[min(max((tv - D0) // H, 0), ND * 24 - 1)]
        if not np.isfinite(b): continue
        last = tv; out.append([tv + L, 0, float(SP[i] * np.exp(b)), tv])
    for j in range(len(out)):
        out[j][1] = min(out[j + 1][0] if j + 1 < len(out) else 10**18, out[j][3] + L + 60000)
    return out

def side_fill(side, p, ahead, t0, t1, tmin):
    """первое заполнение стороны в [t0,t1): side=+1 bid (тейкер-продажа size<0), -1 ask. Возврат (t, цена, как) или None"""
    i0 = int(np.searchsorted(TT, max(t0, tmin), side='left')); i1 = int(np.searchsorted(TT, t1, side='left'))
    if i1 <= i0: return None
    sz = TSZ[i0:i1]; px = TPX[i0:i1]; t = TT[i0:i1]
    if side > 0: m = sz < 0; thru = m & (px < p - 1e-12 * p); lvl = m & (np.abs(px - p) <= 1e-9 * p)
    else: m = sz > 0; thru = m & (px > p + 1e-12 * p); lvl = m & (np.abs(px - p) <= 1e-9 * p)
    best = None
    ti = np.nonzero(thru)[0]
    if len(ti): best = (int(t[ti[0]]), float(px[ti[0]]) if False else p, 'thru')  # заполнены по нашей цене
    if np.isfinite(ahead):
        li = np.nonzero(lvl)[0]
        if len(li):
            cum = np.cumsum(np.abs(sz[li])); ok = np.nonzero(cum >= ahead + 1)[0]
            if len(ok):
                c = (int(t[li[ok[0]]]), p, 'level')
                if best is None or c[0] < best[0]: best = c
    return best

def simulate(ST, bh, k, T, want_eps=False):
    iv = intervals(ST, bh); eps = []; cnt = dict(n_iv=len(iv), rej_age=0, rej_cross=0, exit_thin=0, drop_exit_age=0, open_end=0)
    j = 0; tpos = D0 + DAY; dayU = lambda t: int((t - D0) // DAY)
    quotes = {}
    def qinfo(jj):
        if jj in quotes: return quotes[jj]
        act, end, line, tv = iv[jj]; g = gate_at(act); res = (None, None)
        if g < 0: cnt['rej_age'] += 1; quotes[jj] = res; return res
        bp = np.floor(line * (1 - k) / TICK) * TICK; ap = np.ceil(line * (1 + k) / TICK) * TICK
        if bp >= GA[g]: bp_ = None; cnt['rej_cross'] += 1
        else: bp_ = (bp, 0.0 if bp > GB[g] else (float(GBQ[g]) if abs(bp - GB[g]) <= 1e-9 * bp else np.inf))
        if ap <= GB[g]: ap_ = None; cnt['rej_cross'] += 1
        else: ap_ = (ap, 0.0 if ap < GA[g] else (float(GAQ[g]) if abs(ap - GA[g]) <= 1e-9 * ap else np.inf))
        quotes[jj] = (bp_, ap_); return quotes[jj]
    while j < len(iv):
        act, end, line, tv = iv[j]
        if act < tpos:
            if end <= tpos: j += 1; continue
        bq_, aq_ = qinfo(j); cand = []
        t0 = max(act, tpos)
        if bq_: f = side_fill(+1, bq_[0], bq_[1], t0, end, t0); f and cand.append((f[0], +1, f[1], f[2]))
        if aq_: f = side_fill(-1, aq_[0], aq_[1], t0, end, t0); f and cand.append((f[0], -1, f[1], f[2]))
        if not cand: j += 1; continue
        tf, s, pe, how = min(cand)  # s=+1 long (bid filled)
        # выход: встречная сторона (в этом и следующих интервалах) до tf+T, иначе таймаут рыночно на tf+T+L
        tdl = tf + T * 1000; jx = j; ex = None; sxq = 'timeout'
        while jx < len(iv) and iv[jx][0] <= tdl:
            xact, xend, xline, xtv = iv[jx]; qb, qa = qinfo(jx); oq = qa if s > 0 else qb
            if oq:
                f = side_fill(-s, oq[0], oq[1], max(xact, tf + 1), min(xend, tdl), tf + 1)
                if f: ex = f; break
            jx += 1
        if ex: tx, px_, how2 = ex; kind = 'opp'
        else:
            gx = gate_at(tdl + L)
            if gx < 0: cnt['drop_exit_age'] += 1; j += 1; tpos = tf + 1; continue
            px_ = GB[gx] if s > 0 else GA[gx]; q_ = GBQ[gx] if s > 0 else GAQ[gx]
            if not q_ >= 1: cnt['exit_thin'] += 1
            tx = tdl + L; kind = 'timeout'; how2 = 'mkt'
        g = s * (np.log(px_) - np.log(pe)) * 1e4
        fund = bool(((SETTLE > tf) & (SETTLE <= tx)).any())
        fresh = tf - tv <= 1000 + L  # котировка поставлена <= 1 с назад (после свопа) — вероятное заглядывание
        gi = gate_at(tf); gm = float(np.log(GM[gi]) - np.log(line)) * 1e4 if gi >= 0 else np.nan
        eps.append((tf, dayU(tf), s, g, kind, tx - tf, fund, fresh, how, how2, j, jx if ex else -1, pe, float(px_), gm, tv, line))
        tpos = tx + 1
        while j < len(iv) and iv[j][1] <= tpos: j += 1
    return eps, cnt

def cost(e, tk):  # справочно: комиссии по типу выхода
    m, t = tk
    return m + (m if e[4] == 'opp' else t)
def summ(eps, ceps):
    if not eps: return None
    g = np.array([e[3] for e in eps]); day = np.array([e[1] for e in eps]); cg = np.array([e[3] for e in ceps]) if ceps else np.array([]); cd = np.array([e[1] for e in ceps]) if ceps else np.array([])
    days = sorted(set(day.tolist())); rng = np.random.default_rng(409); bs = []
    for _ in range(2000):
        pick = rng.choice(days, len(days)); mg = np.concatenate([g[day == d] for d in pick])
        mc = np.concatenate([cg[cd == d] for d in pick]) if len(cg) else np.array([])
        bs.append(mg.mean() - (mc.mean() if len(mc) else 0.0))
    pd = {d: (int((day == d).sum()), float(g[day == d].mean())) for d in days}
    good = [d for d in days if dayok[d]]; pos = sum(1 for d in good if pd[d][1] > 0)
    fr = np.array([e[7] for e in eps]); sp = {}
    for nm, m in (('fresh', fr), ('stale', ~fr)):
        sp[f'n_{nm}'] = int(m.sum()); sp[f'gross_{nm}'] = float(g[m].mean()) if m.any() else np.nan
    opp = np.array([e[4] == 'opp' for e in eps]); long_ = np.array([e[2] > 0 for e in eps])
    return dict(**sp, vis=VIS, n=len(g), gross=float(g.mean()), med=float(np.median(g)), ctrl_n=len(cg), ctrl=float(cg.mean()) if len(cg) else np.nan,
                excess=float(g.mean() - (cg.mean() if len(cg) else 0.0)), ci_lo=float(np.percentile(bs, 2.5)), ci_hi=float(np.percentile(bs, 97.5)),
                share_opp=float(opp.mean()), share_long=float(long_.mean()), ref_mm_disc=float(g.mean() - np.mean([cost(e, (1.3, 3.25)) for e in eps])),
                ref_vip0=float(g.mean() - np.mean([cost(e, (2.0, 5.0)) for e in eps])), n_fund=int(sum(e[6] for e in eps)), n_days=len(days), good_days=len(good), pos_good=pos,
                per_day=';'.join(f'{d}:{pd[d][0]}:{pd[d][1]:.1f}' for d in days))

ST_M = ST0; BH_M = basis(ST_M)
ST_C = ST0 + SHIFT3H; BH_C = basis(ST_C)  # линия отстаёт на 3 ч
rows = []; crow = []; main = None; cmain = None
for k in KS:
    for T in TS:
        eps, cnt = simulate(ST_M, BH_M, k, T); ceps, ccnt = simulate(ST_C, BH_C, k, T)
        crow.append(dict(k=k, T=T, **cnt)); s = summ(eps, ceps)
        if s: rows.append(dict(k=k, T=T, **s))
        if (k, T) == MAIN: main, cmain = eps, ceps
import csv
def wr(fn, rs):
    if not rs: return
    with open(fn, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rs[0].keys())); w.writeheader(); w.writerows(rs)
wr(outp + '-cells.csv', [dict(coin=coin, **r) for r in rows]); wr(outp + '-counts.csv', [dict(coin=coin, tick=TICK, good_days=sum(dayok), **r) for r in crow])
if main: wr(outp + '-eps.csv', [dict(coin=coin, t_ms=e[0], day=e[1], s=e[2], gross_bp=e[3], kind=e[4], hold_ms=e[5], fund_cross=e[6], fresh=e[7], in_how=e[8], out_how=e[9], pe=e[12], px=e[13], gate_vs_line_bp=e[14]) for e in main])
with open(outp + '-manual.txt', 'w') as f:
    f.write(f'{coin}: 3 эпизода главной клетки k={MAIN[0]} T={MAIN[1]} L={L} A={A} V={VIS} (тик {TICK})\n')
    for e in ([main[0], main[len(main) // 2], main[-1]] if main and len(main) >= 3 else (main or [])):
        tf = e[0]; i0 = int(np.searchsorted(TT, tf - 2000)); i1 = int(np.searchsorted(TT, tf + 200)); kd = int(np.searchsorted(ST_M, e[15], side='left'))
        f.write(f'\n--- вход t={tf} ({dt.datetime.fromtimestamp(tf / 1000, dt.timezone.utc):%d.%m %H:%M:%S.%f}) {"long (bid)" if e[2] > 0 else "short (ask)"} цена {e[12]:.10g} как={e[8]}; котировка от свопа tv={e[15]} (ts_с={sw[kd,0]:.0f} блок={sw[kd,1]:.0f} цена DEX={SP[kd]:.10g}), линия с базисом {e[16]:.10g}\n')
        gk = gate_at(tf); f.write(f'gev2 на входе: bid={GB[gk]} ask={GA[gk]} bq={GBQ[gk]} aq={GAQ[gk]}\n' if gk >= 0 else 'gev2 на входе: не жив\n')
        f.write('сделки [-2 с; +0,2 с]:\n' + '\n'.join(f'  {TT[i]} size={TSZ[i]:.0f} px={TPX[i]:.10g}' for i in range(max(i0, i1 - 12), i1)) + '\n')
        f.write(f'выход {e[4]}/{e[9]} через {e[5]} мс по {e[13]:.10g}; валовый {e[3]:.2f} бп; fund_cross={e[6]}; fresh={e[7]}; Gate относительно линии на входе {e[14]:.1f} бп\n')
print(coin, 'ok', len(main or []), 'эп. главной; контроль', len(cmain or []), 'сутки годные', sum(dayok))
