#!/usr/bin/env python3
"""TK-402: лидер ICNT|FS (ema1h, full, k=2, D=100, T=60 с, A=1 с) глазами: повтор клетки C-ядра на питоне с журналом сделок (ядро сделки не пишет).
Сверка с ядром: часовые cnt и sg из s2s1000/ICNT.npz. Затем печать N сделок суток DAYS (по убыванию gross): состояния обеих книг, возраст, BBO в $ (A8).
Запуск (calc): TK402_MK=spot /data/tk0380/npvenv/bin/python tk402-trades-eye.py ICNT 2026-10-16,2026-10-17 3"""
import sys, os, importlib.util, datetime as dt
import numpy as np
D0 = os.path.dirname(os.path.abspath(__file__))
sp = importlib.util.spec_from_file_location('s2', D0 + '/tk402-step2.py'); S2 = importlib.util.module_from_spec(sp); sp.loader.exec_module(S2)
LIB = S2.LIB
coin, show_days, NSHOW = sys.argv[1], sys.argv[2].split(','), int(sys.argv[3])
QUANTO = float(os.environ.get('GATE_QUANTO', '10'))  # Gate: монет на контракт (ICNT_USDT: 10)
A = 1000; D = 100; T = 60000; MINHOLD = 1000; THR = 2.0 * S2.CIRCLE['full']; ALL = [f'2026-10-{x}' for x in range(15, 23)]
sym = [r['bybit_spot'] for r in __import__('csv').DictReader(open('/data/tk0402/stage1.tsv', encoding='utf-8'), delimiter='\t') if r['coin'] == coin][0]
gs = [S2.gate_day(coin, x) for x in ALL]; bs = [S2.bybit_day(sym, x, 'spot') for x in ALL]
Gfull = np.concatenate([S2.np.load(f) for f in sorted(__import__('glob').glob(f'{S2.GEV}/{coin}/2026101???.npy')) if len(S2.np.load(f))]); Gfull = Gfull[np.argsort(Gfull[:, 0], kind='stable')]
Bfull = np.concatenate([np.loadtxt(f'{S2.BEV}/spot/{x}/{sym}-{x}.csv.gz', delimiter=',', skiprows=1, dtype=np.int64, ndmin=2) for x in ALL])
g = tuple(np.concatenate([x[i] for x in gs]) for i in range(3)); b = tuple(np.concatenate([x[i] for x in bs]) for i in range(3))
bad, why = S2.bad_intervals(coin, sym, ALL)
tp = S2.tape(g, b, bad, A); ts, gb, ga, bb, ba, ok, d, gbw, gaw, lgt, lbt = tp
rk, ds = LIB.ranks(d, ok > 0); an = LIB.anchor(LIB.load(), ts, d, ok, rk, ds, 1, S2.HR)
M = len(ts); t0 = int(ts[0]); tend = int(ts[-1])
def fr(j, t): return (t - lgt[j]) <= A and (t - lbt[j]) <= A
side = np.zeros(M, np.int8); ed = np.full(M, np.nan)
mid = (gb + ga + bb + ba) / 4
with np.errstate(all='ignore'):
    e1 = (bb - ga) / mid * 1e4 + an; e2 = (gb - ba) / mid * 1e4 - an
for i in np.flatnonzero((ok > 0) & np.isfinite(an) & np.isfinite(d) & (np.abs(lgt) >= 0)):
    if fr(i, ts[i]): side[i] = 1 if e1[i] >= e2[i] else 2; ed[i] = e1[i] if side[i] == 1 else e2[i]
dev = np.where(side > 0, d - an, np.nan)
n1 = np.full(M + 1, M, np.int64); n2 = np.full(M + 1, M, np.int64)
for i in range(M - 1, -1, -1):
    n1[i] = i if (np.isfinite(dev[i]) and dev[i] >= 0) else n1[i + 1]; n2[i] = i if (np.isfinite(dev[i]) and dev[i] <= 0) else n2[i + 1]
cand = np.flatnonzero((side > 0) & (ed > THR)); ci = 0; pos = 0; trades = []
asof = lambda t: int(np.searchsorted(ts, t, side='right')) - 1
while True:
    while ci < len(cand) and cand[ci] < pos: ci += 1
    if ci >= len(cand): break
    i = int(cand[ci]); t = int(ts[i]); te = t + D
    if te + MINHOLD > tend: break
    je = asof(te)
    if je < 0 or not ok[je] or not fr(je, te): pos = i + 1; continue
    i0 = int(np.searchsorted(ts, te + MINHOLD, side='left')); nx = M if i0 >= M else int((n1 if side[i] == 1 else n2)[i0])
    tx = int(ts[nx]) if nx < M else 1 << 60; E = min(te + T, tx); x = E + D
    if x > tend: break
    jx = asof(x)
    if jx < 0 or not ok[jx] or not fr(jx, x): pos = i + 1; continue
    if E == te + T and E < tx:
        jd = asof(E)
        if jd < 0 or not ok[jd] or not fr(jd, E): pos = i + 1; continue
    if side[i] == 1: gr = ((gbw[jx] / gaw[je] - 1) + (1 - ba[jx] / bb[je])) * 1e4
    else: gr = ((1 - gaw[jx] / gbw[je]) + (bb[jx] / ba[je] - 1)) * 1e4
    trades.append((i, je, jx, int(side[i]), gr, t, te, x, E == te + T and E < tx)); pos = max(int(np.searchsorted(ts, x + 1, side='left')), i + 1)
# сверка с ядром
z = np.load(f'/data/tk0402/s2s1000/{coin}.npz'); gi = list(z['grid']).index('full,2.0,100,60000')
cnt_core = z['s0_ema1h_cnt'][gi]; sg_core = z['s0_ema1h_sg'][gi]; h0 = int(z['s0_t0'][0])
cnt_py = np.zeros(len(cnt_core), int); sg_py = np.zeros(len(cnt_core))
for tr in trades: h = (tr[6] - h0) // 3600000; cnt_py[h] += 1; sg_py[h] += tr[4]
print('сверка: сделок python', len(trades), 'ядро', int(cnt_core.sum()), '| часы cnt равны:', bool((cnt_py == cnt_core).all()), '| макс |sg py-ядро| %.2e бп' % np.abs(sg_py - sg_core).max(), '| sum gross py %.2f ядро %.2f' % (sg_py.sum(), sg_core.sum()))
fmt = lambda ms: dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime('%d.%m %H:%M:%S.') + '%03d' % (ms % 1000)
def day_of(ms): return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime('%Y-%m-%d')
bytick = lambda r: (r[1] * S2.TK[('spot', sym)], r[2] * S2.TK[('spot', sym)])
def rows(arr, t_lo, t_hi, kind):
    m = (arr[:, 0] >= t_lo) & (arr[:, 0] <= t_hi); out = []
    for r in arr[m][:12]:
        if kind == 'b': out.append('   %s bid %.4f(%d) ask %.4f(%d)' % (fmt(int(r[0])), r[1] * S2.TK[('spot', sym)], r[3], r[2] * S2.TK[('spot', sym)], r[4]))
        else: out.append('   %s bid %.4f(%d к.) ask %.4f(%d к.)' % (fmt(int(r[0])), r[1], r[3], r[2], r[4]))
    return out
sel = sorted([tr for tr in trades if day_of(tr[6]) in show_days], key=lambda r: -r[4])
print('сделок в сутках', show_days, len(sel), ' | gross: медиана %.1f, макс %.1f бп' % (np.median([r[4] for r in sel]), max(r[4] for r in sel)))
for tr in sel[:NSHOW]:
    i, je, jx, sd, gr, t, te, x, byto = tr
    print('\n=== сделка %s side=%d (%s) gross %.1f бп; решение %s, вход %s, выход %s (%s), держание %.1f с' % (coin, sd, 'лонг Gate / шорт Bybit-спот' if sd == 1 else 'шорт Gate / лонг Bybit-спот', gr, fmt(t), fmt(te), fmt(x), 'тайм-аут T' if byto else 'возврат опоры', (x - te) / 1000))
    for nm, j in (('решение', i), ('вход', je), ('выход', jx)):
        tt = {'решение': t, 'вход': te, 'выход': x}[nm]
        gq = (Gfull[np.searchsorted(Gfull[:, 0], ts[j], side='right') - 1]); bq_ = (Bfull[np.searchsorted(Bfull[:, 0], ts[j], side='right') - 1])
        print(' %s @%s: Gate %.4f/%.4f (возраст %d мс, bq %d к.=%d ICNT, aq %d к.=%d ICNT) | Bybit %.4f/%.4f (возраст %d мс, bq %d, aq %d ICNT) | d %.1f бп, опора %.1f' % (
            nm, fmt(tt), gb[j], ga[j], tt - lgt[j], gq[3], gq[3] * QUANTO, gq[4], gq[4] * QUANTO, bb[j], ba[j], tt - lbt[j], bq_[3], bq_[4], d[j], an[j]))
    # A8: $ на BBO против объёма: ноги входа — Gate покупка (side1) по aq / шорт Bybit по bq; выход — обратные
    gE = Gfull[np.searchsorted(Gfull[:, 0], ts[je], side='right') - 1]; bE = Bfull[np.searchsorted(Bfull[:, 0], ts[je], side='right') - 1]
    gX = Gfull[np.searchsorted(Gfull[:, 0], ts[jx], side='right') - 1]; bX = Bfull[np.searchsorted(Bfull[:, 0], ts[jx], side='right') - 1]
    if sd == 1: legs = [('вход: Gate buy aq', gE[4] * QUANTO * gE[2]), ('вход: Bybit sell bq', bE[3] * bE[1] * S2.TK[('spot', sym)]), ('выход: Gate sell bq', gX[3] * QUANTO * gX[1]), ('выход: Bybit buy aq', bX[4] * bX[2] * S2.TK[('spot', sym)])]
    else: legs = [('вход: Gate sell bq', gE[3] * QUANTO * gE[1]), ('вход: Bybit buy aq', bE[4] * bE[2] * S2.TK[('spot', sym)]), ('выход: Gate buy aq', gX[4] * QUANTO * gX[2]), ('выход: Bybit sell bq', bX[3] * bX[1] * S2.TK[('spot', sym)])]
    print(' A8, $ на лучшем уровне ноги: ' + '; '.join('%s %.0f$' % l for l in legs) + ' | минимум %.0f$' % min(l[1] for l in legs))
    for nm, tt in (('вход', te), ('выход', x)):
        print(' ' + nm + ' — события Gate ±1,5 с:'); [print(s) for s in rows(Gfull, tt - 1500, tt + 1500, 'g')]
        print(' ' + nm + ' — события Bybit-спот ±1,5 с:'); [print(s) for s in rows(Bfull, tt - 1500, tt + 1500, 'b')]
# по всем сделкам клетки: запас входа до ближайшего изменения Bybit-котировки после te и минимум $ на лучшем уровне 4 ног
tb = Bfull[:, 0]; mg = []; mn = []; byd = {}
for tr in trades:
    i, je, jx, sd, gr, t, te, x, byto = tr
    k = int(np.searchsorted(tb, te, side='right')); mg.append(int(tb[k] - te) if k < len(tb) else -1)
    gE = Gfull[np.searchsorted(Gfull[:, 0], ts[je], side='right') - 1]; bE = Bfull[np.searchsorted(Bfull[:, 0], ts[je], side='right') - 1]
    gX = Gfull[np.searchsorted(Gfull[:, 0], ts[jx], side='right') - 1]; bX = Bfull[np.searchsorted(Bfull[:, 0], ts[jx], side='right') - 1]
    tk = S2.TK[('spot', sym)]
    if sd == 1: v = [gE[4] * QUANTO * gE[2], bE[3] * bE[1] * tk, gX[3] * QUANTO * gX[1], bX[4] * bX[2] * tk]
    else: v = [gE[3] * QUANTO * gE[1], bE[4] * bE[2] * tk, gX[4] * QUANTO * gX[2], bX[3] * bX[1] * tk]
    mn.append(min(v))
mg = np.array(mg); mn = np.array(mn); gr_ = np.array([tr[4] for tr in trades])
print('\nВСЕ %d сделок клетки: запас входа до смены Bybit-котировки, мс: p25/p50/p75 = %s; доля <100 мс: %.2f; <50 мс: %.2f' % (len(trades), np.percentile(mg, [25, 50, 75]).round(0).tolist(), (mg < 100).mean(), (mg < 50).mean()))
print('минимум $ на лучшем уровне 4 ног: p25/p50/p75 = %s; доля < 100 $: %.2f; < 500 $: %.2f' % (np.percentile(mn, [25, 50, 75]).round(0).tolist(), (mn < 100).mean(), (mn < 500).mean()))
print('сделки gross>100 бп: %d (%.0f%% суммы gross); запас<100 мс у них: %.2f' % ((gr_ > 100).sum(), 100 * gr_[gr_ > 100].sum() / gr_.sum(), (mg[gr_ > 100] < 100).mean()))
