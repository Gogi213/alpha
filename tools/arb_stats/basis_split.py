"""Разделение разницы цен между биржами по масштабам времени (уровень / волна / выбросы) — признаки одной пары (A, B) на событиях (мс).

Только измерение сигнала: без P&L, комиссий и кругов. Единицы — % (не bps). Времена — мс; сутки — GMT+4 (границы внутри окна).
Ряды пары: середина m = (bid_A + ask_A) / (bid_B + ask_B) − 1, Sell = bid_A / ask_B − 1, Buy = ask_A / bid_B − 1 (viking xarb_base §2).
Что считается (pair_features):
  mins  — минутная таблица (медиана, последнее, мин, макс середины; max Sell, min Buy; число событий) — для всего «медленного» (лестница
          масштабов, ACF, спектр, волна, фандинг) считается потом без возврата к сырью;
  lt    — гистограммы времён жизни отклонений на логарифмической шкале (1 мс … 10⁸ мс): от опорного уровня REFS (скользящая медиана
          минутных медиан по окну W мин по центру; медиана суток; …) порог x ∈ XS, вид «mid» (середина) и «exec» (Sell − ref / ref − Buy);
          жизнь = от первого события ≥ x до первого события, когда середина вернулась к опоре (как core.episodes); по суткам начала;
  sf    — структурная функция: p50/p90/p99 |m(t+τ) − m(t)| по сетке 1 с, по суткам, τ = 1 с … 12 ч.
"""
import warnings

import numpy as np

import core

TZ_MS = 4 * 3600_000
DAY_MS = 86_400_000
GRID_MS = 1000
REFS = ("w5", "w10", "w20", "w40", "w60", "w120", "w240", "w480", "day")   # опорные уровни для времён жизни (w<N> — окно N мин по центру)
REF_W = {"w5": 5, "w10": 10, "w20": 20, "w40": 40, "w60": 60, "w120": 120, "w240": 240, "w480": 480}
XS = (0.03, 0.05, 0.1, 0.2, 0.3, 0.5, 1.0)              # пороги отклонения, %
KINDS = ("mid", "exec")
LT_EDGES = 10.0 ** np.arange(0, 8.01, 0.25)             # мс: 1 мс … 10⁸ мс (27,8 ч), шаг 10^0,25
NB = len(LT_EDGES) + 1
LAGS_S = (1, 2, 3, 5, 8, 12, 20, 30, 45, 60, 90, 120, 180, 300, 450, 600, 900, 1200, 1800, 2700, 3600, 5400, 7200, 10800, 14400, 21600, 28800, 43200)
SF_Q = (50, 90, 99)


def day_edges(t0, t1):
    """Границы суток GMT+4 внутри окна: [t0, b1, …, t1]."""
    first = (t0 + TZ_MS) // DAY_MS + 1
    last = (t1 - 1 + TZ_MS) // DAY_MS
    return [t0] + [d * DAY_MS - TZ_MS for d in range(first, last + 1)] + [t1]


def rolling_med_centred_np(x, w):
    """Скользящая медиана минутного ряда x (NaN — нет данных) по окну w минут по центру; NaN, если в окне меньше max(1, w // 2) значений (numpy)."""
    n = len(x)
    h = w // 2
    pad = np.r_[np.full(h, np.nan), x, np.full(w - 1 - h, np.nan)]
    win = np.lib.stride_tricks.sliding_window_view(pad, w)[:n]
    cnt = np.sum(~np.isnan(win), axis=1)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        r = np.nanmedian(win, axis=1)
    r[cnt < max(1, w // 2)] = np.nan
    return r


def rolling_med_centred(x, w):
    """То же, быстро через pandas (окно [i − w//2, i + w − 1 − w//2]); без pandas — numpy-вариант."""
    try:
        import pandas as pd
    except Exception:  # noqa: BLE001 — pandas может быть сломан бинарной несовместимостью
        return rolling_med_centred_np(x, w)
    h = w // 2
    s = pd.Series(np.asarray(x, float)).rolling(w, min_periods=max(1, w // 2)).median().shift(-(w - 1 - h))
    return s.to_numpy()


def ep_groups(ei, xi):
    """Эпизоды «вход → выход»: ei — индексы, где отклонение ≥ x (отсортированы), xi — индексы, где середина вернулась к опоре.
    Вход — первый ei после прошлого выхода; выход — первый xi строго позже входа. -> (индексы входов, индексы выходов или −1)."""
    if not len(ei):
        return ei, ei
    q = np.searchsorted(xi, ei, "right")
    head = np.flatnonzero(np.r_[True, q[1:] != q[:-1]])
    qs = q[head]
    if len(xi):
        end = np.where(qs < len(xi), xi[np.minimum(qs, len(xi) - 1)], -1)
    else:
        end = np.full(len(qs), -1, np.int64)
    return ei[head], end


def merge_dead(dead, exs, margin_ms=120_000):
    """Пропуски сбора нескольких бирж, объединённые с запасом: (начала, концы) или None."""
    if not dead:
        return None
    iv = sorted([(s, e + margin_ms) for ex in exs for s, e in dead.get(ex, [])])
    out = []
    for s, e in iv:
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    if not out:
        return None
    return np.array([o[0] for o in out], np.int64), np.array([o[1] for o in out], np.int64)


def in_dead(ts, te, dead):
    """Маска: интервал [ts, te] пересекает пропуск."""
    if dead is None:
        return np.zeros(len(ts), bool)
    ds, de = dead
    i = np.searchsorted(de, ts, "right")
    return (i < len(ds)) & (ds[np.minimum(i, len(ds) - 1)] <= te)


def pair_features(A, B, t0, t1, dead=None):
    """Признаки пары за окно [t0, t1) (t0, t1 — на границах минуты). A, B — книги (core.clean: e, bid, ask). None — данных мало.
    dead — (начала, концы) пропусков обеих бирж: сетка и минутная таблица там NaN, эпизоды, пересекающие пропуск, выбрасываются."""
    T, S0, Bu0, ia, ib = core.pair_series(A, B, t1)
    k = T >= t0
    T, ia, ib = T[k], ia[k], ib[k]
    if len(T) < 50:
        return None
    mid = (A["bid"][ia] + A["ask"][ia]) / (B["bid"][ib] + B["ask"][ib]) - 1.0
    M = mid * 100.0
    S = S0[k] / 100.0
    Bu = Bu0[k] / 100.0
    n_min = int((t1 - t0) // 60000)
    m0 = t0 // 60000
    edges = day_edges(t0, t1)
    nday = len(edges) - 1
    # --- сетка 1 с (ZOH) и минутная таблица
    n_s = n_min * 60
    gt = t0 + np.arange(n_s, dtype=np.int64) * GRID_MS
    gi = np.searchsorted(T, gt, "right") - 1
    gv = gi >= 0
    giz = np.maximum(gi, 0)
    Mg = np.where(gv, M[giz], np.nan)
    Sg = np.where(gv, S[giz], np.nan)
    Bg = np.where(gv, Bu[giz], np.nan)
    if dead is not None:
        dm = in_dead(gt, gt, dead)
        Mg[dm] = np.nan
        Sg[dm] = np.nan
        Bg[dm] = np.nan
    R = lambda a: a.reshape(n_min, 60)  # noqa: E731
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        med = np.nanmedian(R(Mg), axis=1)
        lo, hi = np.nanmin(R(Mg), axis=1), np.nanmax(R(Mg), axis=1)
        smax, bmin = np.nanmax(R(Sg), axis=1), np.nanmin(R(Bg), axis=1)
    last = R(Mg)[:, -1]
    mi = (T // 60000 - m0)
    head = np.flatnonzero(np.r_[True, mi[1:] != mi[:-1]])
    mm = mi[head]
    ok = (mm >= 0) & (mm < n_min)
    hh, mm = head[ok], mm[ok]
    nev = np.zeros(n_min, np.int32)
    nev[mm] = np.add.reduceat(np.ones(len(T), np.int32), head)[ok]
    fmax, fmin = np.fmax, np.fmin
    hi[mm] = fmax(hi[mm], np.maximum.reduceat(M, head)[ok])
    lo[mm] = fmin(lo[mm], np.minimum.reduceat(M, head)[ok])
    smax[mm] = fmax(smax[mm], np.maximum.reduceat(S, head)[ok])
    bmin[mm] = fmin(bmin[mm], np.minimum.reduceat(Bu, head)[ok])
    mins = {"med": med.astype(np.float32), "last": last.astype(np.float32), "lo": lo.astype(np.float32), "hi": hi.astype(np.float32),
            "smax": smax.astype(np.float32), "bmin": bmin.astype(np.float32), "n": nev}
    # --- опоры
    day_of_min = np.minimum(np.searchsorted(np.array(edges[1:-1], np.int64), (m0 + np.arange(n_min)) * 60000, "right"), nday - 1)
    day_of_ev = np.searchsorted(np.array(edges[1:-1], np.int64), T, "right")
    gday = np.searchsorted(np.array(edges[1:-1], np.int64), gt, "right")
    ref_day = np.array([np.nanmedian(Mg[gday == d]) if np.isfinite(Mg[gday == d]).any() else np.nan for d in range(nday)])
    refs_min = {name: rolling_med_centred(med.astype(np.float64), w) for name, w in REF_W.items()}
    # --- времена жизни отклонений
    lt = np.zeros((len(REFS), len(XS), len(KINDS), nday, NB), np.int32)
    lt_open = np.zeros((len(REFS), len(XS), len(KINDS), nday, NB), np.int32)     # не вышедшие до конца окна: прошло времени с входа (нижняя оценка жизни)
    cens = np.zeros((len(REFS), len(XS), len(KINDS), nday), np.int32)
    occ = np.full((len(REFS), len(XS), nday), np.nan, np.float32)                # доля времени (сетка 1 с) с |середина − опора| ≥ x
    gmin = np.arange(n_s) // 60
    Tmin = np.clip(T // 60000 - m0, 0, n_min - 1)
    gedge_pre = [int((b - t0) // GRID_MS) for b in edges]
    for r, name in enumerate(REFS):
        refT = ref_day[day_of_ev] if name == "day" else refs_min[name][Tmin]
        refg = ref_day[gday] if name == "day" else refs_min[name][gmin]
        adev = np.abs(Mg - refg)
        for xi_, x in enumerate(XS):
            for d in range(nday):
                seg = adev[gedge_pre[d]:gedge_pre[d + 1]]
                v = np.isfinite(seg)
                if v.any():
                    occ[r, xi_, d] = float((seg[v] >= x).mean())
        dev = M - refT
        Es = S - refT
        Eb = refT - Bu
        xu, xd = np.flatnonzero(dev <= 0), np.flatnonzero(dev >= 0)
        for xi_, x in enumerate(XS):
            for ki, (up, dn) in enumerate(((np.flatnonzero(dev >= x), np.flatnonzero(dev <= -x)), (np.flatnonzero(Es >= x), np.flatnonzero(Eb >= x)))):
                for ent, ext in ((up, xu), (dn, xd)):
                    s, e = ep_groups(ent, ext)
                    if not len(s):
                        continue
                    closed = e >= 0
                    te = np.where(closed, T[np.maximum(e, 0)], t1)
                    bad = in_dead(T[s], te, dead)
                    keep = ~bad
                    d_s = day_of_ev[s]
                    for d in range(nday):
                        sel = keep & (d_s == d)
                        c = sel & closed
                        if c.any():
                            tau = (T[e[c]] - T[s[c]]).astype(np.float64)
                            b = np.searchsorted(LT_EDGES, tau, "right")
                            lt[r, xi_, ki, d] += np.bincount(b, minlength=NB).astype(np.int32)
                        o = sel & ~closed
                        if o.any():
                            b = np.searchsorted(LT_EDGES, (t1 - T[s[o]]).astype(np.float64), "right")
                            lt_open[r, xi_, ki, d] += np.bincount(b, minlength=NB).astype(np.int32)
                            cens[r, xi_, ki, d] += int(o.sum())
    # --- структурная функция по сетке 1 с (по суткам)
    gedge = [int((b - t0) // GRID_MS) for b in edges]
    sf = np.full((nday, len(LAGS_S), len(SF_Q)), np.nan, np.float32)
    sfn = np.zeros((nday, len(LAGS_S)), np.int64)
    for d in range(nday):
        a, b = gedge[d], gedge[d + 1]
        seg = Mg[a:b]
        for li, L in enumerate(LAGS_S):
            if b - a - L < 100:
                continue
            inc = np.abs(seg[L:] - seg[:-L])
            inc = inc[np.isfinite(inc)]
            if len(inc) < 100:
                continue
            sf[d, li] = np.percentile(inc, SF_Q)
            sfn[d, li] = len(inc)
    # --- итоги по суткам: покрытие, уровень
    cover = np.array([np.isfinite(Mg[gedge[d]:gedge[d + 1]]).mean() if gedge[d + 1] > gedge[d] else 0.0 for d in range(nday)])
    gapg = Bg - Sg                                    # Buy − Sell ≈ спред A + спред B (%): шум сделки пары
    gap_d = np.array([np.nanmedian(gapg[gedge[d]:gedge[d + 1]]) if np.isfinite(gapg[gedge[d]:gedge[d + 1]]).any() else np.nan for d in range(nday)])
    return {"gap_d": gap_d, "mins": mins, "lt": lt, "lt_open": lt_open, "occ": occ, "cens": cens, "sf": sf, "sfn": sfn, "ref_day": ref_day, "cover": cover, "n_ev": int(len(T)),
            "ev_per_s": float(len(T) / max(1, (t1 - t0) / 1000)), "level_all": float(np.nanmedian(Mg)) if np.isfinite(Mg).any() else None,
            "day_of_min": day_of_min.astype(np.int8)}
