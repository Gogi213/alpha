"""Качели пары бирж (A, B): «круги» между +N и −N от нуля на исполнимых ценах, подтверждённые через L мс.

Только измерение сигнала (без бэктеста стратегии). Единицы — % (1 bps = 0,01 %). Времена — мс, время биржи E; сутки — GMT+4 в отчёте.
Цены пары — формулы viking (xarb_base §2): Sell = bid_A / ask_B − 1 (продать A, купить B), Buy = ask_A / bid_B − 1 (купить A, продать B); Sell ≤ Buy.
Пересечение «вверх»: Sell впервые (после состояния < N) ≥ +N; «вниз»: Buy впервые ≤ −N. Подтверждено на задержке L: условие всё ещё
выполняется на последнем событии ≤ t + L (цены берутся на t + L; L = 300 — основная, 50 — вторая, 0 — сырая). Качели = два подряд идущих
подтверждённых пересечения противоположных сторон (повторы одной стороны подряд не считаются: позиция уже открыта); каждая пара подряд
идущих = один круг с валовым итогом Sell_исп − Buy_исп ≥ 2N. Круги идут цепочкой (закрытие на −N открывает зеркальный круг).
Качели относятся к окну по времени закрытия (подтверждённое второе пересечение). Качели и пересечения, затрагивающие пропуск сбора (с запасом), не считаются.
«Доля времени за ±N» — время, когда Sell ≥ +N или Buy ≤ −N (по событиям, без пропусков), на часовых корзинах от t0.
"""
import numpy as np

import core
from basis_split import in_dead

NS = (0.1, 0.2, 0.3, 0.5, 1.0)     # % — размер качели
LAGS_S = (1, 2, 5, 10, 20, 30, 60, 120, 300, 600, 1200, 1800, 3600, 7200, 14400, 21600)   # с — лаги автокорреляции середины
LS = (0, 50, 300, 10000)                  # мс — задержка подтверждения
HOUR = 3_600_000


def _crossings(state, T, t0):
    """Индексы первых событий серий state == True (предыдущее событие — False), T ≥ t0. Первое событие серии не считается (когда оно появилось, неизвестно)."""
    idx = np.flatnonzero(state[1:] & ~state[:-1]) + 1
    return idx[T[idx] >= t0]


def _confirm(idx, state, T, L, t1, dead):
    """Подтверждение на t + L: индекс события, на котором берётся цена (последнее ≤ t + L), или отброс."""
    ct = T[idx] + L
    j = np.searchsorted(T, ct, "right") - 1
    ok = (ct < t1) & state[j] & ~in_dead(T[idx], ct, dead)
    return idx[ok], j[ok]


def swings_for(T, S, Bu, hi, lo, L, t0, t1, dead):
    """Качели одной настройки. -> (t_закрытия − t0 (мс), длительность (мс), валовый итог (%), первая сторона ±1) как массивы."""
    ih, jh = _confirm(_crossings(hi, T, t0), hi, T, L, t1, dead)
    il, jl = _confirm(_crossings(lo, T, t0), lo, T, L, t1, dead)
    tc = np.concatenate([T[ih], T[il]])
    side = np.concatenate([np.ones(len(ih), np.int8), -np.ones(len(il), np.int8)])
    px = np.concatenate([S[jh], Bu[jl]])
    o = np.argsort(tc, kind="stable")
    tc, side, px = tc[o], side[o], px[o]
    if len(tc):
        k = np.r_[True, side[1:] != side[:-1]]
        tc, side, px = tc[k], side[k], px[k]
    if len(tc) < 2:
        z = np.zeros(0)
        return z.astype(np.int64), z.astype(np.int64), z.astype(np.float32), z.astype(np.int8)
    t_in, t_out = tc[:-1], tc[1:]
    gross = side[:-1] * (px[:-1] - px[1:])
    keep = ~in_dead(t_in, t_out + L, dead)
    return (t_out[keep] + L - t0).astype(np.int64), (t_out[keep] - t_in[keep]).astype(np.int64), gross[keep].astype(np.float32), side[:-1][keep]


def _cum_fn(T, w):
    """x → накопленное время, когда w (0/1 по событиям) истинно, до x (кусочно-линейно; до первого события — 0)."""
    dt = np.diff(T)
    cum = np.r_[0.0, np.cumsum(dt * w[:-1])]

    def f(x):
        x = np.asarray(x, np.int64)
        i = np.clip(np.searchsorted(T, x, "right") - 1, 0, len(T) - 1)
        return cum[i] + np.maximum(x - T[i], 0) * w[i]
    return f


def hourly_time(T, S, Bu, N, t0, nb, dead):
    """-> (время за ±N по часовым корзинам от t0 (мс), покрытое время корзин (мс)); пропуски сбора вычтены. Покрытие — с первого события пары."""
    edges = t0 + np.arange(nb + 1, dtype=np.int64) * HOUR
    beyond = ((S >= N) | (Bu <= -N)).astype(float)
    cb = _cum_fn(T, beyond)
    val = cb(edges)
    cov = np.maximum(edges - T[0], 0).astype(float)
    if dead is not None:
        for ds, de in zip(*dead):
            val = val - (cb(np.clip(edges, ds, de)) - cb(ds))
            cov = cov - np.maximum(np.minimum(edges, de) - max(ds, T[0]), 0)
    return np.diff(val), np.diff(cov)


def grid_zoh(T, v, t0, step, n, offset=0):
    """Значения v (ZOH, последнее событие ≤ момента) на сетке t0 + offset + k·step, k = 0..n−1; NaN до первого события."""
    g = t0 + offset + np.arange(n, dtype=np.int64) * step
    i = np.searchsorted(T, g, "right") - 1
    out = v[np.maximum(i, 0)].astype(np.float64)
    out[i < 0] = np.nan
    return out, g


def acf_at(x, lags):
    """Автокорреляция ряда x (NaN — вне данных, только в начале; уже заполненные пропуски — средним) на заданных лагах (в шагах); FFT."""
    ok = np.flatnonzero(~np.isnan(x))
    if len(ok) < 5 * max(lags):
        return None
    x = x[ok[0]:]
    x = x - x.mean()
    n = len(x)
    f = np.fft.rfft(x, 1 << int(np.ceil(np.log2(2 * n))))
    ac = np.fft.irfft(f * np.conj(f))[:n]
    v = ac[0] / n
    return np.array([ac[l] / (n - l) / v if l < n else np.nan for l in lags]), float(v)


def pair_swings(A, B, t0, t1, dead=None):
    """Качели пары за окно [t0, t1). A, B — книги (core.clean: e, bid, ask). None — данных мало.
    -> {"sw": {(индекс N, L): (t_закрытия − t0, длительность, валовый итог, сторона)}, "bey": [N][корзина] мс, "cov": [корзина] мс, "nev", "t0e"}."""
    T, S0, Bu0, _, _ = core.pair_series(A, B, t1)
    k0 = max(int(np.searchsorted(T, t0, "right")) - 1, 0)
    T, S, Bu = T[k0:], S0[k0:] / 100.0, Bu0[k0:] / 100.0
    if len(T) < 50:
        return None
    nb = int((t1 - t0) // HOUR)
    out = {"sw": {}, "bey": np.zeros((len(NS), nb), np.float64), "nev": int((T >= t0).sum()), "t0e": int(T[0] - t0)}
    for iN, N in enumerate(NS):
        hi, lo = S >= N, Bu <= -N
        for L in LS:
            out["sw"][(iN, L)] = swings_for(T, S, Bu, hi, lo, L, t0, t1, dead)
        out["bey"][iN], cov = hourly_time(T, S, Bu, N, t0, nb, dead)
    out["cov"] = cov
    # середина исполнимых цен m = (Sell + Buy)/2 и полуспред h = (Buy − Sell)/2: минутная сетка (конец минуты) и сетка 1 с для автокорреляции
    m, h = (S + Bu) / 2, (Bu - S) / 2
    nmin = int((t1 - t0) // 60_000)
    out["mm"] = grid_zoh(T, m, t0, 60_000, nmin, 60_000)[0].astype(np.float32)
    out["hm"] = grid_zoh(T, h, t0, 60_000, nmin, 60_000)[0].astype(np.float32)
    x1, g1 = grid_zoh(T, m, t0, 1000, int((t1 - t0) // 1000))
    if dead is not None:
        dd = in_dead(g1, g1, dead)
        x1[dd] = np.nanmean(x1)
    r = acf_at(x1, LAGS_S)
    out["acf"], out["var1s"] = (r if r is not None else (None, None))
    out["bey"] = out["bey"].astype(np.float32)
    out["cov"] = out["cov"].astype(np.float32)
    return out
