#!/usr/bin/env python3
"""TK-394 П-28 шаг 2ms = step2f на мс-задержках D = 0/100/250/500/1000 мс (TK-399: событийный BBO Bybit; Gate — 0,1-с BBO tk394-gate-bbo-ms.py, метка = конец корзины).
Решение по состоянию на метке секунды t (как в step2f); вход по BBO на t*1000+D мс (последняя строка с t_ms<=T), выход: решение на метке s >= t+1+D//1000, исполнение на s*1000+D;
тайм-аут T с от целой секунды входа. Допуск: Gate — возраст последней строки <= 1000 мс на T и час книги валиден (holes2); Bybit — посекундная маска tk0369 (gap=0) на секунде T
(событийные файлы не различают «книга не менялась» и «разрыв ленты»). Остальное — функции step2f (опоры, фандинг, издержки) без изменений.
Запуск (calc): tk394-step2ms.py OUTDIR COIN... -> OUTDIR/COIN.npz + COIN.json; ячейка (pair,mode,anchor,scen,k,delay_ms,T)."""
import sys, os, json, glob, importlib.util
import numpy as np
D0 = os.path.dirname(os.path.abspath(__file__))
sp = importlib.util.spec_from_file_location('s2f', os.path.join(D0, 'tk394-step2f.py'))
F = importlib.util.module_from_spec(sp); sp.loader.exec_module(F)
M = F.M; S = F.S; T0 = F.T0; N = F.N; NH = F.NH; FEE = F.FEE
DELAYS = [0, 100, 250, 500, 1000]
GBM = '/data/tk0394/bboms'; BEV = '/data/tk0399/bbo'
def gate_ms(coin):
    fs = sorted(glob.glob(f'{GBM}/{coin}/*.npy'))
    if not fs: return None
    a = np.concatenate([np.load(f) for f in fs]); a = a[np.argsort(a[:, 0], kind='stable')]
    return a[:, 0].astype(np.int64), a[:, 1], a[:, 2]
def bybit_ev(sym, mk):
    tick = S.TK[(mk, sym)]; m = S.mult(sym); ts = []; bid = []; ask = []
    for d in S.DAYS:
        f = f'{BEV}/{mk}/{d}/{sym}-{d}.csv.gz'
        if not os.path.exists(f): continue
        a = np.loadtxt(f, delimiter=',', skiprows=1, usecols=(0, 1, 2), dtype=np.int64, ndmin=2)
        if len(a) == 0: continue
        b = np.where(a[:, 1] > 0, a[:, 1] * tick / m, np.nan); k = np.where(a[:, 2] > 0, a[:, 2] * tick / m, np.nan)
        ts.append(a[:, 0]); bid.append(b); ask.append(k)
    if not ts: return None
    return np.concatenate(ts), np.concatenate(bid), np.concatenate(ask)
def asof_ms(ts, val, T):
    i = np.searchsorted(ts, T, side='right') - 1; ok = i >= 0; ii = np.clip(i, 0, None)
    return np.where(ok, val[ii], np.nan), np.where(ok, T - ts[ii], 10**12)
def run_pair(pair, Q, ev0, fb, GFEE, gfc, bfc, fa):
    gb, gk, bb, bk = Q[0][:4]
    mid = np.where(ev0, (gb + gk + bb + bk) / 4, np.nan)
    d = np.where(ev0, (np.log((gb + gk) / 2) - np.log((bb + bk) / 2)) * 1e4, np.nan)
    e1 = (bb - gk) / mid * 1e4; e2 = (gb - bk) / mid * 1e4
    A = F.anchors(d, fa)
    cells = []; sums = []; fsums = []; cnts = []; eps = []; cr1 = []; cr2 = []; drops = []
    ar = np.arange(N); BIG = 10**9
    for anc, b in A.items():
        mode = 'A' if anc == 'A0' else 'B'
        ed1 = e1 + b; ed2 = e2 - b
        side = np.where(ed1 >= ed2, 1, 2); ed = np.where(side == 1, ed1, ed2)
        dev = d - b
        c1 = np.where(np.isfinite(dev) & ev0 & (dev >= 0), ar, BIG); c2 = np.where(np.isfinite(dev) & ev0 & (dev <= 0), ar, BIG)
        n1 = np.minimum.accumulate(c1[::-1])[::-1]; n2 = np.minimum.accumulate(c2[::-1])[::-1]
        for sc, g in F.SCEN:
            circle = 2 * (GFEE * g + fb)
            for k in F.THR:
                cand = np.where(np.isfinite(ed) & (ed > k * circle) & ev0)[0]
                for Dl in DELAYS:
                    qb, qk, rb, rk, evD = Q[Dl]; ds = Dl // 1000; k0 = 1 + ds
                    for T in F.TIMEOUTS:
                        sg = np.zeros(NH); fg = np.zeros(NH); ct = np.zeros(NH, np.int64); ep = np.zeros(NH, np.int64)
                        x1 = np.zeros(NH, np.int64); x2 = np.zeros(NH, np.int64); dr = 0
                        pos = 0; last_exit = -10**9
                        while True:
                            j = np.searchsorted(cand, pos)
                            if j >= len(cand): break
                            t = int(cand[j]); te = t + ds
                            if te + k0 + 1 >= N: break
                            sd = int(side[t])
                            if not evD[t]: pos = t + 1; dr += 1; continue
                            nx = n1[t + k0] if sd == 1 else n2[t + k0]
                            s = min(int(nx), te + T)
                            if s >= N or not evD[s]: pos = t + 1; dr += 1; continue
                            x = s + ds
                            if sd == 1: gr = ((qb[s] / qk[t] - 1) + (1 - rk[s] / rb[t])) * 1e4
                            else: gr = ((1 - qk[s] / qb[t]) + (rb[s] / rk[t] - 1)) * 1e4
                            h = te // 3600
                            fr = F.fsum(gfc, te, x) - (F.fsum(bfc, te, x) if bfc is not None else 0.0)
                            sg[h] += gr; fg[h] += -fr * 1e4 if sd == 1 else fr * 1e4; ct[h] += 1
                            if te - last_exit >= 5: ep[h] += 1
                            nb = x // 28800 - te // 28800
                            if nb > 0:
                                if sd == 1: x1[h] += nb
                                else: x2[h] += nb
                            last_exit = x; pos = max(x, t) + 1
                        cells.append((pair, mode, anc, sc, k, Dl, T)); sums.append(sg); fsums.append(fg); cnts.append(ct); eps.append(ep); cr1.append(x1); cr2.append(x2); drops.append(dr)
    return cells, sums, cnts, eps, cr1, cr2, drops, fsums
def coin_run(coin):
    gm = gate_ms(coin)
    if gm is None: return None
    syms = {}
    for mk in ('fut', 'spot'):
        for pre in ('', '1000', '1000000', '10000'):
            s = f'{pre}{coin}USDT'
            if (mk, s) in S.TK and glob.glob(f'{BEV}/{mk}/2026-10-15/{s}-2026-10-15.csv.gz'): syms[mk] = s
    grid = np.arange(T0, T0 + N, dtype=np.int64)
    vh = F.valid_hours(coin); ar = np.arange(N)
    gQ = {}; gok = {}
    for Dl in DELAYS:
        T = grid * 1000 + Dl
        a, age = asof_ms(gm[0], gm[1], T); b, _ = asof_ms(gm[0], gm[2], T)
        gQ[Dl] = (a, b); gok[Dl] = (age <= 1000) & vh[np.clip(ar + Dl // 1000, 0, N - 1)]
    out = dict(cells=[], sums=[], cnts=[], eps=[], cr1=[], cr2=[], drops=[], fsums=[])
    fd = F.FUND[coin]; gfc = F.fcum(fd['g']); bfc = F.fcum(fd['b']) if fd['b'] else None
    fa = F.fund_anchor(fd['g'], fd['b'], N)
    for pair, mk, fee in (('FF', 'fut', FEE['BF']), ('FS', 'spot', FEE['BS'])):
        if mk not in syms: continue
        r = S.bybit(syms[mk], mk)
        e = bybit_ev(syms[mk], mk)
        if r is None or e is None: continue
        v, age1 = M.asof_age(r[0], r[2], grid); bsec = (age1 <= 1) & np.isfinite(v)
        Q = {}
        for Dl in DELAYS:
            T = grid * 1000 + Dl
            rb, _ = asof_ms(e[0], e[1], T); rk, _ = asof_ms(e[0], e[2], T)
            ok = gok[Dl] & bsec[np.clip(ar + Dl // 1000, 0, N - 1)] & np.isfinite(rb) & np.isfinite(rk) & np.isfinite(gQ[Dl][0]) & np.isfinite(gQ[Dl][1])
            Q[Dl] = (gQ[Dl][0], gQ[Dl][1], rb, rk, ok)
        res = run_pair(pair, Q, Q[0][4], fee, FEE['GF'], gfc, bfc if pair == 'FF' else None, fa if pair == 'FF' else F.fund_anchor(fd['g'], [], N))
        for key, val in zip(out.keys(), res): out[key] += val
    return out
if __name__ == '__main__':
    od = sys.argv[1]; os.makedirs(od, exist_ok=True)
    for c in sys.argv[2:]:
        o = coin_run(c)
        if o is None or not o['cells']: print(c, 'skip', flush=True); continue
        np.savez_compressed(f'{od}/{c}.npz', sums=np.array(o['sums']), cnts=np.array(o['cnts'], dtype=np.int32), eps=np.array(o['eps'], dtype=np.int32),
                            cr1=np.array(o['cr1'], dtype=np.int32), cr2=np.array(o['cr2'], dtype=np.int32), drops=np.array(o['drops']), fs=np.array(o['fsums']))
        json.dump([list(x) for x in o['cells']], open(f'{od}/{c}.json', 'w'))
        print(c, len(o['cells']), int(np.sum(o['cnts'])), flush=True)
