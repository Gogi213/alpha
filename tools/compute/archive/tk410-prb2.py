#!/usr/bin/env python3
"""TK-410 проба B2, вторая итерация (возврат Судьи 26.10): эпизод руками, разложение живости, W6/Q2, Q5 догон, охват mid.
Запуск (calc): GEV=/data/tk0393/tk401/gev2all python tk410-prb2.py B2 B2-raw.csv OUT.txt"""
import sys, os, glob, csv, datetime as dt
import numpy as np
coin, rawf, outf = sys.argv[1:4]
D0 = 1792022400 * 1000; ND = 8; DEND = D0 + ND * 86400000; H = 3600000; DAY = 86400000
GEV = os.environ.get('GEV', '/data/tk0393/tk401/gev2all')
VIS, L, A_IN, NET = 1000, 250, 5000, 2.6
out = open(outf, 'w')
def P(*a):
    print(*a, file=out); out.flush()
raw = np.genfromtxt(rawf, delimiter=',', names=True, dtype=None, encoding=None)
_, ui = np.unique(np.stack([raw['block'], raw['logidx']], 1), axis=0, return_index=True)
raw = raw[np.sort(ui)]; raw = raw[np.argsort(raw['ts'], kind='stable')]
TS = raw['ts'].astype(np.int64); LM = np.log(raw['mid_raw'].astype(np.float64)); LQ = raw['liq0'].astype(np.float64)
m_in = (TS * 1000 >= D0) & (TS * 1000 < DEND); TS, LM, LQ = TS[m_in], LM[m_in], LQ[m_in]; TV = TS * 1000 + VIS
hv = np.zeros(ND * 24 + 2, bool); parts = []
for f in sorted(glob.glob(f'{GEV}/{coin}/??????????.npy')):
    try: n = int(open(f[:-4] + '.n').read())
    except OSError: n = -1
    if n < 100: continue
    d = dt.datetime.strptime(os.path.basename(f)[:-4], '%Y%m%d%H').replace(tzinfo=dt.timezone.utc)
    hi = int(d.timestamp()) // 3600 - D0 // H
    if 0 <= hi < ND * 24: hv[hi] = True; parts.append(np.load(f))
a = np.concatenate(parts); a = a[np.argsort(a[:, 0], kind='stable')]
GT = a[:, 0].astype(np.int64); GB, GA = a[:, 1], a[:, 2]; GOK = np.isfinite(GB) & np.isfinite(GA) & (GB < GA)
GM = np.log((GB + GA) / 2)
def hidx(t): return np.clip((t - D0) // H, 0, ND * 24 - 1)
def gate_at(t, A):
    t = np.asarray(t); k = np.searchsorted(GT, t, side='right') - 1; kc = np.clip(k, 0, None)
    return kc, (k >= 0) & (t - GT[kc] <= A) & hv[hidx(t)] & hv[hidx(GT[kc])] & GOK[kc]
def status(t, A):
    """0 ok, 1 час-дыра (в точке или записи), 2 возраст > A при живом часе"""
    k, ok = gate_at(t, A); k0, ok_inf = gate_at(t, 10 ** 12)
    st = np.where(ok, 0, np.where(~hv[hidx(t)] | ~hv[hidx(GT[k0])], 1, 2)); return st
def resp(t0, h, s, Ain, Aout):
    k0, o0 = gate_at(t0, Ain); k1, o1 = gate_at(t0 + h * 1000, Aout); ok = o0 & o1 & (t0 + h * 1000 < DEND)
    pin = np.where(s > 0, GA[k0], GB[k0]); pout = np.where(s > 0, GB[k1], GA[k1])
    return np.where(ok, s * (np.log(pout) - np.log(pin)) * 1e4, np.nan)

# ---------- 1. эпизод руками
E = 1792531548; TVE = E * 1000 + VIS
P('== 1. ЭПИЗОД', dt.datetime.fromtimestamp(E, dt.timezone.utc), 'UTC (ts=%d)' % E)
sel = np.nonzero((TS >= E - 120) & (TS <= E + 300))[0]
P('DEX свопы -120..+300 с: t_rel, ln(mid)-ln(mid_prev)*1e4 бп, liq0')
for i in sel:
    P('  %+5d с  mid %.8f  d_prev %+8.1f бп  liq %.4g' % (TS[i] - E, np.exp(LM[i]), (LM[i] - LM[i - 1]) * 1e4 if i else 0, LQ[i]))
P('Gate gev2 записи tv-60..+60 с (t_rel с от tv, bid, ask, bq, aq):')
for k in np.nonzero((GT >= TVE - 60000) & (GT <= TVE + 60000))[0]:
    P('  %+8.2f  %.5f %.5f  %g %g' % ((GT[k] - TVE) / 1000, GB[k], GA[k], a[k, 3], a[k, 4]))
kk = np.nonzero(GT > TVE)[0]
P('Первая запись gev2 после tv: +%.1f с; час-дыры вокруг: часы эпизода %s' % ((GT[kk[0]] - TVE) / 1000, [bool(hv[int(hidx(TVE + o * 1000))]) for o in (-3600, 0, 3600)]))
gpre = GM[np.searchsorted(GT, TVE - 28000, side='right') - 1]
P('Gate mid до хода (tv-28 с) %.5f; после +10 с %.5f (изм. %+.1f бп); сила хода на DEX: mid %.8f -> %.8f' % (
    np.exp(gpre), np.exp(GM[np.searchsorted(GT, TVE + 10000, side='right') - 1]),
    (GM[np.searchsorted(GT, TVE + 10000, side='right') - 1] - gpre) * 1e4, np.exp(LM[sel[0]]), np.exp(LM[sel[-1]])))

# ---------- 2. живость
P('\n== 2. ЖИВОСТЬ gev2 B2')
live = hv[hidx(GT[1:])] & hv[hidx(GT[:-1])]; gap = np.diff(GT)[live] / 1000
P('промежутки между записями (живые часы): n=%d p50=%.2f p90=%.2f p99=%.1f max=%.0f с; доля времени в промежутках > 5 с: %.3f' % (
    len(gap), np.percentile(gap, 50), np.percentile(gap, 90), np.percentile(gap, 99), gap.max(), gap[gap > 5].sum() / gap.sum()))
W = 60
jlo = np.searchsorted(TS, TS - 2 * W, side='left'); jhi = np.searchsorted(TS, TS - W, side='right') - 1
has = (jhi >= jlo) & (jhi >= 0); X = np.where(has, LM - LM[np.clip(jhi, 0, None)], np.nan)
anch = []; nxt = -1
for i in np.nonzero(has & (np.abs(X) > 0))[0]:
    if TS[i] >= nxt: anch.append(i); nxt = TS[i] + W
anch = np.array(anch, int); s = np.sign(X[anch]).astype(float); ax = np.abs(X[anch]) * 1e4; t0 = TV[anch] + L; day = (TV[anch] - D0) // DAY
st1 = status(TV[anch], A_IN); st2 = status(TS[anch] * 1000 - W * 1000, A_IN)
P('W=60: якорей %d. Точка tv: ok %d, час-дыра %d, возраст>5с(час жив) %d. Точка ts-W: ok %d, дыра %d, возраст>5с %d' % (
    len(anch), (st1 == 0).sum(), (st1 == 1).sum(), (st1 == 2).sum(), (st2 == 0).sum(), (st2 == 1).sum(), (st2 == 2).sum()))
hole = (st1 == 1) | (st2 == 1); old = ~hole & ((st1 == 2) | (st2 == 2)); okp = (st1 == 0) & (st2 == 0)
P('отброс по живости (pre): дыра %d (%.0f %%), возраст>5с в живом часе %d (%.0f %%), оба ok %d' % (
    hole.sum(), 100 * hole.mean(), old.sum(), 100 * old.mean(), okp.sum()))
age = np.array([(TV[i] - GT[np.searchsorted(GT, TV[i], side='right') - 1]) / 1000 for i in anch[old]])
P('возраст последней записи в точке tv у «возраст>5с»: p50=%.1f p90=%.1f с' % (np.percentile(age, 50), np.percentile(age, 90)) if len(age) else 'нет')
def main(Apre, Ain, Aout, h=60):
    k_tv, o1 = gate_at(TV[anch], Apre); k_pw, o2 = gate_at(TS[anch] * 1000 - W * 1000, Apre)
    Pm = s * (GM[k_tv] - GM[k_pw]) * 1e4; okP = o1 & o2; strat0 = okP & (Pm < 0.25 * ax)
    q1 = np.percentile(ax, 25); qa = np.nonzero(ax <= q1)[0]
    ex = resp(t0, h, s, Ain, Aout); cx = resp(t0[qa], h, s[qa], Ain, Aout)
    cm = {d: np.nanmean(cx[day[qa] == d]) if np.isfinite(cx[day[qa] == d]).any() else np.nan for d in range(ND)}
    m = strat0 & np.isfinite(ex); exc = ex[m] - np.array([cm[d] for d in day[m]]); dd = sorted(set(day[m].tolist()))
    rng = np.random.default_rng(410); bs = []
    for _ in range(1000):
        pk = rng.choice(dd, len(dd)); idx = np.concatenate([np.nonzero(day[m] == d)[0] for d in pk]); bs.append(np.nanmean(exc[idx]))
    return int(okP.sum()), int(m.sum()), len(dd), float(np.nanmean(exc)), float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))
for lab, args in (('A=5с (главное)', (A_IN, A_IN, A_IN)), ('A=60с «час жив, возраст<=60 с»', (60000, 60000, 60000))):
    r = main(*args); P('W=h=60 %s: pre-ok %d, strat0 с выходом n=%d, суток %d, exc_exec %+.2f бп, ДИ [%+.2f; %+.2f]' % ((lab,) + r))
rr = main(A_IN, A_IN, A_IN); P('сверка с прежним TK-407/409: те вердикты на посекундных недействительны (TK-403); число сопоставить не с чем — отличие не определено (не «не делалась»: нет пригодного прежнего числа)')

# ---------- 3. W6 / Q2 / Q3-разложение
P('\n== 3. Q2 расхождение d (W6): базис = медиана d за 24 ч, взвешенная по длительности, без заглядывания')
ev_t = np.unique(np.concatenate([TV, GT[hv[hidx(GT)]]])); ev_t = ev_t[(ev_t >= D0) & (ev_t < DEND)]
kg = np.searchsorted(GT, ev_t, side='right') - 1; kd = np.searchsorted(TV, ev_t, side='right') - 1
okg = (kg >= 0) & (ev_t - GT[np.clip(kg, 0, None)] <= 60000) & hv[hidx(ev_t)] & GOK[np.clip(kg, 0, None)]
okd = (kd >= 0) & (ev_t - TV[np.clip(kd, 0, None)] <= 60000)
ok = okg & okd; t = ev_t[ok]; lg = GM[kg[ok]]; ld = LM[kd[ok]]; d = lg - ld
w = np.minimum(np.diff(np.append(t, t[-1] + 1000)), 60000).astype(float)
P('событий пары (свопы+записи gev2): %d, с живыми обеими сторонами (A_dex=A_gate=60 с): %d' % (len(ev_t), len(t)))
def wmed(v, ww):
    o = np.argsort(v); c = np.cumsum(ww[o]); return v[o][np.searchsorted(c, c[-1] / 2)]
grid = np.arange(D0, DEND, 600000); basis = np.full(len(t), np.nan)
for g in grid:
    lo = np.searchsorted(t, g - DAY, side='left'); hi = np.searchsorted(t, g, side='left')
    if hi - lo < 50: continue
    b = wmed(d[lo:hi], w[lo:hi]); i0 = np.searchsorted(t, g, side='left'); i1 = np.searchsorted(t, g + 600000, side='left'); basis[i0:i1] = b
nobase = ~np.isfinite(basis); P('W6: событий без истории базиса (первые сутки/дыры/<50 событий в окне): %d из %d (%.1f %%); сырой d (без базиса) p50 |d| = %.1f бп, p90 = %.1f бп (включает курс WBNB/USD — см. §6)' % (
    nobase.sum(), len(t), 100 * nobase.mean(), np.percentile(np.abs(d) * 1e4, 50), np.percentile(np.abs(d) * 1e4, 90)))
da = (d - basis)
P('d после базиса (по длительности, события с базисом): p50 |d| = %.1f бп, p90 = %.1f, p99 = %.1f' % tuple(np.percentile(np.abs(da[~nobase]) * 1e4, [50, 90, 99])))
hr = hidx(t)
P('Q2 эпизоды: D(бп) | эпизодов | сошлось | цензура | p50 время до схождения с (KM) | p90 | доля Gate в схождении (медиана)')
for D in (30, 50, 100, 200, 500, 1000):
    Dl = D / 1e4; i = 0; eps = []
    while i < len(t):
        if nobase[i] or abs(da[i]) < Dl: i += 1; continue
        j = i + 1; res = None
        while j < len(t):
            if t[j] - t[i] > DAY: res = 'T'; break
            if not hv[hr[j]] or t[j] - t[j - 1] > 600000: res = 'C'; break
            if not nobase[j] and abs(da[j]) <= Dl / 2: res = 'S'; break
            j += 1
        if res is None: res = 'C'; j = len(t) - 1
        sg = np.sign(da[i]); dG = lg[j] - lg[i]; dD = ld[j] - ld[i]; dd_ = -(dG - dD) * sg
        eps.append((res, (t[j] - t[i]) / 1000, (-dG * sg / dd_) if res == 'S' and dd_ > 0 else np.nan)); i = j + 1
    if not eps: P('  %5d | 0' % D); continue
    tm = np.array([e[1] for e in eps]); ev = np.array([e[0] == 'S' for e in eps]); o = np.argsort(tm); surv = 1.0; kmt = []
    nrisk = len(tm)
    for q in o:
        if ev[q]: surv *= 1 - 1 / nrisk; kmt.append((tm[q], surv))
        nrisk -= 1
    def km(p):
        for tt, sv in kmt:
            if sv <= 1 - p: return tt
        return float('nan')
    sh = np.array([e[2] for e in eps if e[0] == 'S' and np.isfinite(e[2])])
    P('  %5d | %4d | %4d | %4d | %7.1f | %7.1f | %s' % (D, len(eps), ev.sum(), sum(e[0] != 'S' and e[0] != 'T' for e in eps), km(.5), km(.9),
                                                       ('%.2f (n=%d)' % (np.median(sh), len(sh))) if len(sh) else '—'))
P('(T = не сошлось за 24 ч; в KM как цензура по времени; Q2 на одной монете — описание, не вывод)')

# ---------- 4. Q5 догон
P('\n== 4. Q5: догон. Эпизоды Q1 с |X| >= 2 %% при W=30,60 (ходы >=5 %% — отмечены); доля догона = s*(lnG(t) - lnG(ts-W))/|X| по событиям gev2, A=60 с')
seen = set()
for Wq in (10, 30, 60, 300, 1800):
    jl = np.searchsorted(TS, TS - 2 * Wq, side='left'); jh = np.searchsorted(TS, TS - Wq, side='right') - 1
    hs = (jh >= jl) & (jh >= 0); Xq = np.where(hs, LM - LM[np.clip(jh, 0, None)], np.nan); nx = -1
    for i in np.nonzero(hs & (np.abs(Xq) * 1e4 >= 200))[0]:
        if TS[i] < nx: continue
        nx = TS[i] + Wq
        tvq = TV[i]; kpre, okpre = gate_at(np.array([TS[i] * 1000 - Wq * 1000]), 60000)
        if not okpre[0]: P('  W=%d ts=%d X=%+.0f бп: нет живого Gate до хода' % (Wq, TS[i], Xq[i] * 1e4)); continue
        sq = np.sign(Xq[i]); path = sq * (GM - GM[kpre[0]]) / abs(Xq[i])
        kk = np.nonzero((GT > tvq) & (GT <= tvq + DAY))[0]; res = []
        for frac in (.5, .9):
            hit = np.nonzero(path[kk] >= frac)[0]; res.append('%.1f с' % ((GT[kk[hit[0]]] - tvq) / 1000) if len(hit) else 'нет за 24 ч/дыра')
        samp = []
        for o in (1, 10, 60, 300, 1800, 3600):
            k2, ok2 = gate_at(np.array([tvq + o * 1000]), 60000); samp.append('%+.2f' % path[k2[0]] if ok2[0] else 'nan')
        P('  W=%4d %s X=%+7.0f бп%s: доля догона на +1,10,60,300,1800,3600 с = %s; до 50 %%: %s; до 90 %%: %s; swaps в окне %d' % (
            Wq, dt.datetime.fromtimestamp(TS[i], dt.timezone.utc).strftime('%d.%m %H:%M:%S'), Xq[i] * 1e4, ' (>=5 %)' if abs(Xq[i]) >= .05 else '',
            ' '.join(samp), res[0], res[1], int(((TS >= TS[i] - Wq) & (TS <= TS[i])).sum())))
P('(доля >1 — Gate ушёл дальше DEX; <0 — в обратную сторону)')

# ---------- 5. охват mid на бою
P('\n== 5. ОХВАТ W8: пулы боя × источник середины (по спискам TK-393)')
import collections
for fn in ('/data/tk0393/pools-new.csv', '/data/tk0393/pools-old.csv'):
    rows = list(csv.DictReader(open(fn))); c = collections.Counter()
    for r in rows:
        stab = r['quote'] in ('USDT', 'USDC', 'USD', 'DAI', 'FDUSD', 'USDC.e', 'BUSD', 'USD1')
        c[(r['chain'], 'quote стейбл' if stab else 'quote НЕ стейбл')] += 1
    P(os.path.basename(fn), 'пулов', len(rows)); chs = collections.Counter(r['chain'] for r in rows)
    for (ch, q), n in sorted(c.items(), key=lambda x: -x[1])[:14]: P('   %-10s %-16s %d' % (ch, q, n))
    P('   по меткам DEX-версии:', dict(collections.Counter((r.get('labels') or '?') for r in rows)))
nw = 0
for ch in ('arbitrum', 'avalanche', 'base', 'bsc', 'ethereum', 'optimism', 'polygon'):
    f = f'/data/tk0393/sqd/{ch}-swaps/summary.csv'
    if os.path.exists(f):
        ps = set(l.split(',')[0].lower() for l in open(f).read().splitlines()[1:]); nw += len(ps); P('   sqd %s: пулов со свопами в sqd (summary) %d' % (ch, len(ps)))
P('итого EVM-пулов со свопами в sqd (summary):', nw)
out.close()
