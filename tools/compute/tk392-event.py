#!/usr/bin/env python3
"""TK-392: событие «середина DEX сдвинулась на >=X за окно W» -> отклик середины Gate (фьюч BBO / спот по сделкам) на 39 горизонтах.
Запуск (calc, /data/tk0380/npvenv/bin/python): tk392-event.py COIN MIDCSV OUTPREFIX
Сетка 1 с, окно 15.10 00:00 UTC + 8 суток. Середина DEX: последняя после свопа в секунде, ffill. Gate фьюч и спот: последняя цена сделки в секунде, ffill<=60 с (приближение, BBO TK-380 пересечён). Событие: первое пересечение |log D[t]/D[t-W]| >= X в данном знаке; след. событие той же ячейки не раньше t+W.
Отклик h: s*log(G[t+h]/G[t]) (s = знак события), доля = отклик / |ход DEX за окно|; ход DEX после: s*log(D[t+h]/D[t]); контроль: те же t0 в случайных секундах (5 повторов), знак как у события.
Выход: OUTPREFIX-curve.csv (coin,mkt,X,W,dir,day,h,n,...) — day='all' и по суткам по дню t0."""
import sys, gzip, csv, os, numpy as np
coin, midf, outp = sys.argv[1:4]
LEG = sys.argv[4] if len(sys.argv) > 4 else ''  # BNB|ETH: пул с квотой WBNB/WETH -> середина в USD = середина × цена ноги (сделки Gate спот, приближение)
D0 = 1792022400 - 3 * 86400  # 15.10.2026 00:00 UTC (18.10 = 1792022400 в swaps)
ND = 8; N = ND * 86400
HS = [1, 5, 30, 60, 90, 120] + list(range(150, 301, 30)) + list(range(360, 601, 60)) + list(range(900, 3601, 300)) + list(range(4500, 14401, 900))
assert len(HS) == 39
XS = [0.005, 0.01, 0.02, 0.05]; WS = [10, 60, 300]
def ffill(a, lim):
    idx = np.where(~np.isnan(a), np.arange(len(a)), -10 ** 9); idx = np.maximum.accumulate(idx)
    ok = (idx >= 0) & (np.arange(len(a)) - idx <= lim)
    o = np.full(len(a), np.nan); o[ok] = a[idx[ok]]; return o
HDR = 'coin mkt X W dir day h n dex_move_bp gate_resp_bp share dex_after_bp ctrl_gate_bp'.split()
def skip(why):  # пропуск монеты с причиной (rc 0): пустая кривая, чтобы перезапуск не пересчитывал
    with open(outp + '-curve.csv', 'w', newline='') as f: csv.writer(f).writerow(HDR)
    print(coin, 'SKIP:', why); sys.exit(0)
d = np.full(N, np.nan)
for line in gzip.open(midf, 'rt'):
    ts, b, l, m = line.split(','); i = int(ts) - D0
    if 0 <= i < N: d[i] = float(m)   # отсортировано по (ts, block, logidx) -> последний своп секунды
D = ffill(d, 10 ** 9); 
first = np.argmax(~np.isnan(D)) if (~np.isnan(D)).any() else N
D[:first] = np.nan
gate = {}
def trades(kind, c=None):
    sd = f'/srv/alpha-sda/tk372-gate/raw/{kind}/{c or coin}_USDT'
    if not os.path.isdir(sd): return None
    p = np.full(N, np.nan)
    for fn in sorted(os.listdir(sd)):
        for r in csv.DictReader(gzip.open(f'{sd}/{fn}', 'rt')):
            v = float(r['create_time_ms'] or r['create_time']); t = v / 1000 if v > 1e11 else v
            i = int(t) - D0
            if 0 <= i < N: p[i] = float(r['price'])
    return ffill(p, 60)
# CEO 25.10: BBO фьюча TK-380 пересечён (TK-394) -> середина фьюча тоже по последней сделке секунды (приближение, пометка)
if LEG:
    L = trades('spot', LEG)
    if L is None: skip(f'нет сделок спота ноги {LEG}')
    D = D * L; D[:first] = np.nan
    first = int(np.argmax(~np.isnan(D))) if (~np.isnan(D)).any() else N
    D[:first] = np.nan
for kind, key in (('fut', 'fut'), ('spot', 'spot')):
    g = trades(kind)
    if g is not None: gate[key] = g
if N - 14401 <= max(max(WS), first): skip(f'короткий ряд DEX: первая секунда {first} из {N}, контроль не помещается')
rng = np.random.default_rng(392)
rows = []
for mkt, G in gate.items():
    lD = np.log(D); lG = np.log(G)
    for W in WS:
        mv = np.full(N, np.nan); mv[W:] = lD[W:] - lD[:-W]
        for X in XS:
            for sgn, name in ((1, 'up'), (-1, 'down')):
                cand = np.where(sgn * mv >= np.log1p(X))[0]  # порог по логарифму: вниз на -log(1+X) (симметрично)
                ev = []; last = -1
                for t in cand:
                    if t >= last: ev.append(t); last = t + W
                ev = np.array(ev, dtype=int)
                ev = ev[~np.isnan(lG[ev])] if len(ev) else ev
                if len(ev) == 0: continue
                days = ev // 86400
                dexw = sgn * mv[ev]
                resp = np.full((len(ev), 39), np.nan); dafter = np.full((len(ev), 39), np.nan); ctrl = np.full((5, len(ev), 39), np.nan)
                for k, h in enumerate(HS):
                    ok = ev + h < N
                    e2 = ev[ok]
                    resp[ok, k] = sgn * (lG[e2 + h] - lG[e2]); dafter[ok, k] = sgn * (lD[e2 + h] - lD[e2])
                    for r in range(5):
                        c = rng.integers(max(W, first), N - 14401, size=len(ev))
                        ctrl[r, :, k] = sgn * (lG[c + h] - lG[c])
                for dk in ['all'] + sorted(set(days.tolist())):
                    m = np.ones(len(ev), bool) if dk == 'all' else days == dk
                    n = int(m.sum())
                    if n == 0: continue
                    dm = float(np.nanmean(dexw[m]))
                    for k, h in enumerate(HS):
                        rk = resp[m, k]; g = np.nanmean(rk) if (~np.isnan(rk)).any() else np.nan
                        rows.append((coin, mkt, X, W, name, dk, h, n, round(dm * 1e4, 2), round(g * 1e4, 2) if g == g else '',
                                     round(g / dm, 4) if g == g else '', round(float(np.nanmean(dafter[m, k])) * 1e4, 2) if (~np.isnan(dafter[m, k])).any() else '',
                                     round(float(np.nanmean(ctrl[:, m, k])) * 1e4, 2) if (~np.isnan(ctrl[:, m, k])).any() else ''))
with open(outp + '-curve.csv', 'w', newline='') as f:
    w = csv.writer(f); w.writerow(HDR); w.writerows(rows)
print(coin, {k: int(np.isfinite(v).sum()) for k, v in gate.items()}, len(rows), 'rows')
