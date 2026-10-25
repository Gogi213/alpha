#!/usr/bin/env python3
"""TK-394 / П-28 шаг 2 на посекундной сетке (CEO 06:33; 0-500 мс — после TK-399): двуногая сделка Gate x Bybit, режимы A и B.
Запуск (calc): tk394-step2.py OUTDIR COIN... -> OUTDIR/COIN.npz (+ COIN.json мета). Пары FF (Gate фьюч / Bybit фьюч), FS (Gate фьюч / Bybit спот).
Решение по состоянию конца секунды t, вход по BBO на t+D (D = 1 и 2 с), выход: решение на s >= te+1, исполнение на s+D; только тейкеры.
Выход: возврат d=log(midA/midB) к опоре (знак по стороне) или тайм-аут T с после входа. Опоры B: медиана прошлого 5м/30м/1ч/4ч/24ч,
EMA (полупериод 5м/1ч/24ч), медиана прошлых суток (фикс на день, сутки 0 без опоры); режим A: опора 0. Опора «накопленный фандинг» — НЕ в этом проходе.
Порог = k * круг (k = 1/1.5/2; круг = 2*(taker_Gate + taker_Bybit), Gate без скидки и x0,65). Допуск: BBO Gate и Bybit свежие (<=1 с) на t, t+D, s, s+D
и час книги Gate валиден (holes2: окно >= 3500 с). Сделка без допуска на выходе — отброшена (счётчик drop).
Выход: на клетку по 168 часам: сумма gross (бп, до издержек), число сделок, число эпизодов (склейка < 5 с между входом и прошлым выходом),
число пересечений границ выплат фандинга (00/08/16 UTC) по сторонам 1 и 2 (side 1: лонг Gate / шорт Bybit)."""
import sys, os, json, importlib.util, itertools
import numpy as np
D0 = os.path.dirname(os.path.abspath(__file__))
sp = importlib.util.spec_from_file_location('s3', os.path.join(D0, 'tk394-spread3.py'))
M = importlib.util.module_from_spec(sp); sp.loader.exec_module(M)
S = M.S; T0 = M.T0; ND = M.ND; N = M.N; NH = ND * 24
FEE = S.FEE; DISC = 0.65
TIMEOUTS = [1, 5, 30, 60, 90, 120, 3600, 14400]
DELAYS = [1, 2]
THR = [1.0, 1.5, 2.0]
SCEN = [('full', 1.0), ('disc', DISC)]

def ema(d, hl):
    a = 1 - 0.5 ** (1 / hl)
    x = np.where(np.isfinite(d), d, np.nan)
    out = np.full(len(d), np.nan); cur = np.nan
    for i in range(len(d)):
        out[i] = cur                       # только прошлое (< t)
        v = x[i]
        if np.isfinite(v): cur = v if not np.isfinite(cur) else cur + a * (v - cur)
    return out
def prev_day_median(d):
    b = np.full(len(d), np.nan)
    for k in range(1, ND):
        w = d[(k - 1) * 86400:k * 86400]; w = w[np.isfinite(w)]
        if len(w) > 17280: b[k * 86400:(k + 1) * 86400] = np.median(w)
    return b
def anchors(d):
    A = {'A0': np.zeros(len(d))}
    for nm, win, step in (('med5m', 300, 10), ('med30m', 1800, 30), ('med1h', 3600, 60), ('med4h', 14400, 300), ('med24h', 86400, 600)):
        A[nm] = M.past_median(d, win, step)
    for nm, hl in (('ema5m', 300), ('ema1h', 3600), ('ema24h', 86400)): A[nm] = ema(d, hl)
    A['medPrevDay'] = prev_day_median(d)
    return A
def valid_hours(coin):
    j = json.load(open(f'/data/tk0394/holes2/{coin}.json'))
    ok = np.zeros(NH, bool)
    for di, r in enumerate(j['days'][:ND]):
        for h in range(24): ok[di * 24 + h] = r['book_sec_by_hour'][h] >= 3500
    return np.repeat(ok, 3600)
def run_pair(coin, pair, gb, gk, bb, bk, exfresh, fb, GFEE):
    ev = np.isfinite(gb) & np.isfinite(gk) & np.isfinite(bb) & np.isfinite(bk) & exfresh
    mid = np.where(ev, (gb + gk + bb + bk) / 4, np.nan)
    d = np.where(ev, (np.log((gb + gk) / 2) - np.log((bb + bk) / 2)) * 1e4, np.nan)
    e1 = (bb - gk) / mid * 1e4; e2 = (gb - bk) / mid * 1e4     # side 1: купить Gate / продать Bybit; side 2: наоборот
    A = anchors(d)
    cells = []; sums = []; cnts = []; eps = []; cr1 = []; cr2 = []; drops = []
    ar = np.arange(N); BIG = 10**9
    for anc, b in A.items():
        mode = 'A' if anc == 'A0' else 'B'
        ed1 = e1 + b; ed2 = e2 - b
        side = np.where(ed1 >= ed2, 1, 2); ed = np.where(side == 1, ed1, ed2)
        dev = d - b
        c1 = np.where(np.isfinite(dev) & ev & (dev >= 0), ar, BIG); c2 = np.where(np.isfinite(dev) & ev & (dev <= 0), ar, BIG)
        n1 = np.minimum.accumulate(c1[::-1])[::-1]; n2 = np.minimum.accumulate(c2[::-1])[::-1]
        for sc, g in SCEN:
            circle = 2 * (GFEE * g + fb)
            for k in THR:
                thr = k * circle
                sig = np.isfinite(ed) & (ed > thr) & ev
                cand = np.where(sig)[0]
                for Dl in DELAYS:
                    # допуск на момент исполнения: свежесть на t+D (ev на t+D)
                    for T in TIMEOUTS:
                        sg = np.zeros(NH); ct = np.zeros(NH, np.int64); ep = np.zeros(NH, np.int64)
                        x1 = np.zeros(NH, np.int64); x2 = np.zeros(NH, np.int64); dr = 0
                        pos = 0; last_exit = -10**9
                        while True:
                            j = np.searchsorted(cand, pos)
                            if j >= len(cand): break
                            t = int(cand[j]); te = t + Dl
                            if te + 1 + Dl >= N: break
                            sd = int(side[t])
                            if not ev[te]:
                                pos = t + 1; dr += 1; continue
                            nx = n1[te + 1] if sd == 1 else n2[te + 1]
                            s = min(int(nx), te + T); x = s + Dl
                            if x >= N or not ev[x]:
                                pos = t + 1; dr += 1; continue
                            if sd == 1: gr = ((gb[x] / gk[te] - 1) + (1 - bk[x] / bb[te])) * 1e4
                            else: gr = ((1 - gk[x] / gb[te]) + (bb[x] / bk[te] - 1)) * 1e4
                            h = te // 3600
                            sg[h] += gr; ct[h] += 1
                            if te - last_exit >= 5: ep[h] += 1
                            nb = x // 28800 - te // 28800
                            if nb > 0:
                                if sd == 1: x1[h] += nb
                                else: x2[h] += nb
                            last_exit = x; pos = x + 1
                        cells.append((pair, mode, anc, sc, k, Dl, T)); sums.append(sg); cnts.append(ct); eps.append(ep); cr1.append(x1); cr2.append(x2); drops.append(dr)
    return cells, sums, cnts, eps, cr1, cr2, drops
def coin_run(coin):
    gf = S.gate_fut(coin)
    if gf is None: return None
    syms = {}
    for mk in ('fut', 'spot'):
        for pre in ('', '1000', '1000000', '10000'):
            s = f'{pre}{coin}USDT'
            if (mk, s) in S.TK and __import__('glob').glob(f'{S.BYB}/{mk}/2026-10-15/{s}-2026-10-15.csv.gz'): syms[mk] = s
    grid = np.arange(T0, T0 + N, dtype=np.int64)
    gb, ga_age = M.asof_age(gf[0], gf[1], grid); gk, _ = M.asof_age(gf[0], gf[2], grid)
    vh = valid_hours(coin)
    gfresh = (ga_age <= 1) & vh
    out = dict(cells=[], sums=[], cnts=[], eps=[], cr1=[], cr2=[], drops=[])
    for pair, mk, fee in (('FF', 'fut', FEE['BF']), ('FS', 'spot', FEE['BS'])):
        if mk not in syms: continue
        r = S.bybit(syms[mk], mk)
        if r is None: continue
        bb, _ = M.asof_age(r[0], r[1], grid); bk, age = M.asof_age(r[0], r[2], grid)
        fresh = gfresh & (age <= 1) & np.isfinite(bk)
        res = run_pair(coin, pair, gb, gk, bb, bk, fresh, fee, FEE['GF'])
        for key, v in zip(out.keys(), res): out[key] += v
    return out
if __name__ == '__main__':
    od = sys.argv[1]; os.makedirs(od, exist_ok=True)
    for c in sys.argv[2:]:
        o = coin_run(c)
        if o is None or not o['cells']: print(c, 'skip', flush=True); continue
        np.savez_compressed(f'{od}/{c}.npz', sums=np.array(o['sums']), cnts=np.array(o['cnts'], dtype=np.int32), eps=np.array(o['eps'], dtype=np.int32),
                            cr1=np.array(o['cr1'], dtype=np.int32), cr2=np.array(o['cr2'], dtype=np.int32), drops=np.array(o['drops']))
        json.dump([list(x) for x in o['cells']], open(f'{od}/{c}.json', 'w'))
        print(c, len(o['cells']), int(np.sum(o['cnts'])), flush=True)
