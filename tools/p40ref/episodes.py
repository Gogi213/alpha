"""Эталон П-40 (TK-501), шаг 2: сетка событий двух ног -> d -> эпизоды схождения (без стопов, цензура на конце данных).
Вход: book_<A>.npz, book_<B>.npz из book.py (t[мс], o[:, bid, ask, bq1, aq1, bidVWAP, askVWAP, bid5, ask5], цены в тиках 1e-6).
Выход: grid.npz (сетка и d) и episodes_<theta>.csv. Толкования протокола — в отчёте (раздел «Толкования»)."""
import sys
import numpy as np
from numba import njit

SC = 1e6
L_MS = 6 * 3600 * 1000
GAP_MS = 60 * 1000          # правая граница окна уровня: t - 1 мин
W_MS = 60 * 1000            # взвод
THETAS = [5.04, 12.6, 25.2, 50.0, 100.0]


def build_grid(tA, oA, tB, oB, smax_floor_ms=1000):
    """Сетка = объединение моментов событий обеих ног, состояние «после всех событий <= t»."""
    T = np.union1d(tA, tB)
    T = T[T >= max(tA[0], tB[0])]
    ia = np.searchsorted(tA, T, side="right") - 1
    ib = np.searchsorted(tB, T, side="right") - 1
    ageA = T - tA[ia]
    ageB = T - tB[ib]
    smaxA = max(smax_floor_ms, np.percentile(ageA, 99))
    smaxB = max(smax_floor_ms, np.percentile(ageB, 99))
    bA, aA = oA[ia, 0] / SC, oA[ia, 1] / SC
    bB, aB = oB[ib, 0] / SC, oB[ib, 1] / SC
    valid = (ageA <= smaxA) & (ageB <= smaxB) & (bA < aA) & (bB < aB)   # кроссированная книга исключается (§2.3)
    x = np.log(((bA + aA) / 2) / ((bB + aB) / 2))
    return dict(T=T, ia=ia, ib=ib, bA=bA, aA=aA, bB=bB, aB=aB, x=x, valid=valid, smaxA=smaxA, smaxB=smaxB)


@njit(cache=True)
def level_per_second(T, x, valid, L_s, gap_s, min_frac):
    """lvl[s] = медиана x по секундной сетке за секунды [s-L_s, s-gap_s]; проба секунды = последнее состояние <= конца секунды.
    nan, если окно не начинается внутри данных или проб меньше min_frac окна."""
    s0 = T[0] // 1000
    s1 = T[-1] // 1000
    ns = s1 - s0 + 1
    samp = np.full(ns, np.nan)
    j = 0
    n = T.shape[0]
    last = np.nan
    lastv = False
    for s in range(ns):
        end = (s0 + s + 1) * 1000 - 1
        while j < n and T[j] <= end:
            last = x[j]
            lastv = valid[j]
            j += 1
        if lastv:
            samp[s] = last
    lvl = np.full(ns, np.nan)
    win = np.empty(L_s - gap_s + 2)
    cnt = 0
    for s in range(ns):
        a = s - gap_s
        if a >= 0 and not np.isnan(samp[a]):
            v = samp[a]
            lo, hi = 0, cnt
            while lo < hi:
                m = (lo + hi) // 2
                if win[m] < v:
                    lo = m + 1
                else:
                    hi = m
            for k in range(cnt, lo, -1):
                win[k] = win[k - 1]
            win[lo] = v
            cnt += 1
        r = s - L_s - 1
        if r >= 0 and not np.isnan(samp[r]):
            v = samp[r]
            lo, hi = 0, cnt
            while lo < hi:
                m = (lo + hi) // 2
                if win[m] < v:
                    lo = m + 1
                else:
                    hi = m
            for k in range(lo, cnt - 1):
                win[k] = win[k + 1]
            cnt -= 1
        if s - L_s >= 0 and cnt >= min_frac * (L_s - gap_s + 1):
            lvl[s] = win[cnt // 2] if cnt % 2 == 1 else 0.5 * (win[cnt // 2 - 1] + win[cnt // 2])
    return s0, lvl


@njit(cache=True)
def episodes(T, d_bps, ok, bA, aA, bB, aB, theta, W_ms):
    """Эпизоды по порогу theta (bps). ok[k] = d определён и ноги свежие.
    Колонки: t_start, dir, d_in, t_end(-1 цензура), t_half, t_quarter, peak_abs, t_peak, mpu, t_mpu, best, pnl_end, idx_start, idx_end.
    mpu/best/pnl_end — гипотетический вход на старте (продажа на аске дорогой ноги, покупка на бид дешёвой), оценка по исполнимой
    стороне (закрытие шорта = аск, лонга = бид), bps номинала одной ноги, без комиссий."""
    n = T.shape[0]
    res = np.empty((n // 10 + 1000, 14))
    m = 0
    clean_since = -1
    in_ep = False
    dr = 0.0
    d_in = 0.0
    t_half = -1.0
    t_q = -1.0
    peak = 0.0
    t_peak = 0.0
    mpu = 0.0
    t_mpu = 0.0
    best = 0.0
    idx_s = 0
    t_s = 0
    ea = 0.0
    eb = 0.0
    for k in range(n):
        t = T[k]
        if not ok[k]:
            if in_ep:
                res[m, 0] = t_s; res[m, 1] = dr; res[m, 2] = d_in; res[m, 3] = -1.0
                res[m, 4] = t_half; res[m, 5] = t_q; res[m, 6] = peak; res[m, 7] = t_peak
                res[m, 8] = mpu; res[m, 9] = t_mpu; res[m, 10] = best; res[m, 11] = np.nan
                res[m, 12] = idx_s; res[m, 13] = k
                m += 1
                in_ep = False
            clean_since = -1
            continue
        d = d_bps[k]
        ad = abs(d)
        if in_ep:
            if dr > 0:
                pnl = 1e4 * ((ea - aA[k]) / ea + (bB[k] - eb) / eb)
            else:
                pnl = 1e4 * ((ea - aB[k]) / ea + (bA[k] - eb) / eb)
            if pnl < mpu:
                mpu = pnl
                t_mpu = t - t_s
            if pnl > best:
                best = pnl
            dd = dr * d
            if dd > peak:
                peak = dd
                t_peak = t - t_s
            if t_half < 0 and dd <= 0.5 * abs(d_in):
                t_half = t - t_s
            if t_q < 0 and dd <= 0.25 * abs(d_in):
                t_q = t - t_s
            if dd <= 0:
                res[m, 0] = t_s; res[m, 1] = dr; res[m, 2] = d_in; res[m, 3] = t
                res[m, 4] = t_half; res[m, 5] = t_q; res[m, 6] = peak; res[m, 7] = t_peak
                res[m, 8] = mpu; res[m, 9] = t_mpu; res[m, 10] = best; res[m, 11] = pnl
                res[m, 12] = idx_s; res[m, 13] = k
                m += 1
                in_ep = False
                clean_since = t if ad < 0.5 * theta else -1
            continue
        if ad < 0.5 * theta:
            if clean_since < 0:
                clean_since = t
        else:
            if ad >= theta and clean_since >= 0 and clean_since <= t - W_ms:
                in_ep = True
                dr = 1.0 if d > 0 else -1.0
                d_in = d
                t_s = t
                idx_s = k
                t_half = -1.0
                t_q = -1.0
                peak = ad
                t_peak = 0.0
                mpu = 0.0
                t_mpu = 0.0
                best = 0.0
                if dr > 0:
                    ea = aA[k]
                    eb = bB[k]
                else:
                    ea = aB[k]
                    eb = bA[k]
            clean_since = -1
    if in_ep:
        res[m, 0] = t_s; res[m, 1] = dr; res[m, 2] = d_in; res[m, 3] = -1.0
        res[m, 4] = t_half; res[m, 5] = t_q; res[m, 6] = peak; res[m, 7] = t_peak
        res[m, 8] = mpu; res[m, 9] = t_mpu; res[m, 10] = best; res[m, 11] = np.nan
        res[m, 12] = idx_s; res[m, 13] = n
        m += 1
    return res[:m]


def main(bookA, bookB, outdir):
    za, zb = np.load(bookA), np.load(bookB)
    g = build_grid(za["t"], za["o"], zb["t"], zb["o"])
    T = g["T"]
    s0, lvl = level_per_second(T, g["x"], g["valid"], L_MS // 1000, GAP_MS // 1000, 0.9)
    sec = T // 1000 - s0
    L = lvl[sec]
    d1 = (g["x"] - L) * 1e4
    ok = g["valid"] & ~np.isnan(d1)
    print(f"events={len(T)} valid={g['valid'].mean():.4f} ok(d defined)={ok.mean():.4f} smaxA={g['smaxA']:.0f} smaxB={g['smaxB']:.0f}")
    np.savez_compressed(f"{outdir}/grid.npz", T=T, ia=g["ia"], ib=g["ib"], x=g["x"], lvl=L, d1=d1, ok=ok,
                        bA=g["bA"], aA=g["aA"], bB=g["bB"], aB=g["aB"])
    cols = "t_start,dir,d_in,t_end,t_half,t_quarter,peak_abs,t_peak,mpu,t_mpu,best,pnl_end,idx_start,idx_end"
    for th in THETAS:
        e = episodes(T, d1, ok, g["bA"], g["aA"], g["bB"], g["aB"], th, W_MS)
        np.savetxt(f"{outdir}/episodes_{th}.csv", e, delimiter=",", header=cols, comments="", fmt="%.14g")
        cens = int(np.isnan(e[:, 11]).sum()) if len(e) else 0
        print(f"theta={th}: episodes={len(e)} censored={cens}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
