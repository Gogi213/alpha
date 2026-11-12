#!/usr/bin/env python3
"""TK-394 шаг 1, возврат Судьи: отклонение от скользящей медианы d по ПРОШЛОМУ (1 ч, обновление раз в минуту; 24 ч — раз в 10 мин),
свежесть ног (Gate: возраст последнего события BBO <= 1 с на t-1 и t; Bybit: gap=0), эпизоды со слиянием разрывов < 5 с,
по дням отдельно, кто к кому (движение ног за 5 с после сигнала). Пары FF, FS (исполнимые), BB (контроль).
Запуск (calc): tk394-spread3.py OUTDIR COIN... -> OUTDIR/COIN.json (строки coin,pair,base,day,...)."""
import sys, os, json, importlib.util
import numpy as np
sp = importlib.util.spec_from_file_location('s', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tk394-spread.py'))
S = importlib.util.module_from_spec(sp); sp.loader.exec_module(S)
import glob
T0 = 1792022400; ND = 7; N = ND * 86400
GATE_DISC = 0.65   # сценарий: скидка 35 % на комиссию Gate (владелец), не проверена по странице
def past_median(d, win, step):
    """b[t] = медиана d[t-win..t-1] (только прошлое), обновление каждые step секунд; nan если < 20 % окна."""
    b = np.full(len(d), np.nan)
    for s in range(win, len(d), step):
        w = d[s - win:s]; w = w[np.isfinite(w)]
        if len(w) >= win // 5: b[s:s + step] = np.median(w)
    return b
def asof_age(ts_src, val, grid):
    i = np.searchsorted(ts_src, grid, side='right') - 1
    ok = i >= 0; ii = np.clip(i, 0, None)
    age = np.where(ok, grid - ts_src[ii], 10**9)
    return np.where(ok, val[ii], np.nan), age
def merge_eps(idx, gap=5):
    """индексы секунд -> число эпизодов (разрывы < gap склеиваются) и их начала/концы"""
    if len(idx) == 0: return [], []
    br = np.where(np.diff(idx) >= gap)[0]
    st = np.r_[idx[0], idx[br + 1]]; en = np.r_[idx[br], idx[-1]]
    return st, en
def coin_rows(coin):
    gf = S.gate_fut(coin)
    syms = {}
    for mk in ('fut', 'spot'):
        for pre in ('', '1000', '1000000', '10000'):
            s = f'{pre}{coin}USDT'
            if (mk, s) in S.TK and glob.glob(f'{S.BYB}/{mk}/2026-10-15/{s}-2026-10-15.csv.gz'): syms[mk] = s
    if gf is None: return []
    grid = np.arange(T0, T0 + N, dtype=np.int64)
    gb, ga_age = asof_age(gf[0], gf[1], grid); gk, _ = asof_age(gf[0], gf[2], grid)
    gfresh = ga_age <= 1
    L = {}
    for mk, key, fee in (('fut', 'BF', S.FEE['BF']), ('spot', 'BS', S.FEE['BS'])):
        if mk in syms:
            r = S.bybit(syms[mk], mk)
            if r is not None:
                # gap=1 секунды у S.bybit уже NaN (ok); здесь свежесть = не NaN
                bb, _ = asof_age(r[0], r[1], grid); bk, age = asof_age(r[0], r[2], grid)
                L[key] = (bb, bk, fee, (age <= 1) & np.isfinite(bk))
    rows = []
    for pair, key in (('FF', 'BF'), ('FS', 'BS')):
        if key not in L: continue
        bb, bk, fb, bfresh = L[key]
        ca = S.FEE['GF']; cb = fb
        entry = ca + cb; entry_d = ca * GATE_DISC + cb
        ev = np.isfinite(gb) & np.isfinite(gk) & np.isfinite(bb) & np.isfinite(bk) & (ga_age <= 300)   # как в tk394-spread.py: Gate старше 300 с — вне
        mid = np.where(ev, (gb + gk + bb + bk) / 4, np.nan)
        midA = (gb + gk) / 2; midB = (bb + bk) / 2
        d = np.where(ev, (np.log(midA) - np.log(midB)) * 1e4, np.nan)
        e1 = (bb - gk) / mid * 1e4; e2 = (gb - bk) / mid * 1e4     # e1: купить Gate / продать Bybit; e2: продать Gate / купить Bybit
        fresh = ev & gfresh & bfresh
        for base, (win, step) in (('1h', (3600, 60)), ('24h', (86400, 600))):
            b = past_median(d, win, step)
            e1d = e1 + b; e2d = e2 - b          # отклонение исполнимой разницы от базиса, известного к t
            side = np.where(e1d >= e2d, 1, 2)
            ed = np.where(side == 1, e1d, e2d)
            ok_t1 = np.isfinite(ed)
            for di in range(ND):
                lo, hi = di * 86400, (di + 1) * 86400
                sl = slice(lo, hi)
                # сигнал в t-1 (индекс i), исполнение в t=i+1
                i = np.arange(lo, hi - 1)
                raw = ok_t1[i] & (ed[i] > entry)
                frs = raw & fresh[i] & fresh[i + 1]
                gnow = np.where(side[i] == 1, e1d[i + 1], e2d[i + 1])
                row = dict(coin=coin, pair=pair, base=base, day=di, entry=entry, entry_disc=entry_d, n_valid=int(np.sum(ok_t1[sl])),
                           n_raw=int(raw.sum()), n_fresh=int(frs.sum()))
                for nm, m in (('raw', raw), ('fresh', frs)):
                    st, en = merge_eps(np.where(m)[0])
                    row[f'ep_{nm}'] = int(len(st)); row[f'ep_len_med_{nm}'] = float(np.median(en - st + 1)) if len(st) else 0.0
                if frs.sum() > 0:
                    g = gnow[frs]; g = g[np.isfinite(g)]
                    row['g_n'] = int(len(g))
                    if len(g):
                        row['g_mean'] = float(g.mean()); row['g_med'] = float(np.median(g))
                        row['share_g_gt_entry'] = float((g > entry).mean()); row['share_g_gt_entry_disc'] = float((g > entry_d).mean())
                        row['share_g_gt_0'] = float((g > 0).mean())
                        row['net_mean_entry'] = float((g - entry).mean()); row['net_mean_disc'] = float((g - entry_d).mean())
                    # кто к кому: по началам эпизодов — лог-движение каждой ноги за 5 с (t -> t+5) в сторону закрытия расхождения
                    st, _ = merge_eps(np.where(frs)[0])
                    st = np.array([s for s in st if lo + s + 6 < hi + 0 and np.isfinite(midA[lo + s + 6]) and np.isfinite(midB[lo + s + 6])], dtype=int)
                    if len(st):
                        a0 = lo + st + 1; a1 = a0 + 5
                        dA = (np.log(midA[a1]) - np.log(midA[a0])) * 1e4; dB = (np.log(midB[a1]) - np.log(midB[a0])) * 1e4
                        sgn = np.where(side[lo + st] == 1, 1.0, -1.0)   # side 1: Gate дёшево -> закрытие = Gate вверх, Bybit вниз
                        mvA = sgn * dA; mvB = -sgn * dB                 # вклад в закрытие, бп
                        row['close_A_gate_bp'] = float(np.nanmean(mvA)); row['close_B_bybit_bp'] = float(np.nanmean(mvB)); row['close_n'] = int(len(st))
                rows.append(row)
    return rows
if __name__ == '__main__':
    out = sys.argv[1]; os.makedirs(out, exist_ok=True)
    for c in sys.argv[2:]:
        r = coin_rows(c); json.dump(r, open(f'{out}/{c}.json', 'w')); print(c, len(r), flush=True)
