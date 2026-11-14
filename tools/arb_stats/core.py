"""Ядро статистики арбитража между биржами: пара (A, B) по лучшим bid/ask на уровне событий (мс).

Формулы раздвижки — как в viking (strategies/xarb_base.md §2; viking-docs p.calc_method, p.sell):
  Sell = (bid_A / ask_B − 1)·10⁴ bps — продать A по биду, купить B по оферу (исполнимо сразу);
  Buy  = (ask_A / bid_B − 1)·10⁴ bps — купить A по оферу, продать B по биду.
Центр и разброс — viking tools/dashboard.py: center_tw (медиана (Sell+Buy)/2), vol_tw (p90 |середина − центр|), авто-центр
(медиана по минутным барам за окно 60 мин, нужно ≥ max(3, окно/2) баров). Уровни — LEVELS viking (5…30 bps), плюс порог комиссий.
"""
import warnings

import numpy as np

FEE_RT_BPS = 12.6                                # 4 тейкерских ноги × 3,5 bps × 0,9 (preferences п.12, В-47/71/88)
LEVELS = [5.0, 10.0, FEE_RT_BPS, 15.0, 20.0, 30.0]   # bps: уровни viking + порог комиссий
CAP_MS = 60_000                                  # вес интервала не больше 60 с (viking spread_stats)
WIN_MIN = 60                                     # окно центра, мин (viking: авто-центр по умолчанию 60)
NEED_BARS = 30                                   # viking: need = max(3, окно // 2)
HIST_LO, HIST_STEP, HIST_N = -300.0, 0.1, 6000   # гистограмма с шагом 0,1 bps на [−300; +300]
TAU_EDGES_MS = [100, 500, 1000, 5000, 10000, 30000, 60000, 300000, 900000, 3600000]   # границы корзин времени схождения
EDGE_X = [0.0, 0.5, 1, 2, 3, 5, 7.5, 10, 12.6, 15, 20, 30, 50, 100, 200]               # пороги «запас ≥ x bps» (доля времени)


def clean(a):
    """Сортировка по (E, приём), снятие подряд идущих повторов цены и негодных записей. a — dict массивов."""
    o = np.lexsort((a["rx"], a["e"]))
    a = {k: v[o] for k, v in a.items()}
    ok = (a["bid"] > 0) & (a["ask"] > 0) & (a["bid"] <= a["ask"])
    a = {k: v[ok] for k, v in a.items()}
    if len(a["e"]) > 1:
        ch = np.r_[True, (a["bid"][1:] != a["bid"][:-1]) | (a["ask"][1:] != a["ask"][:-1])]
        a = {k: v[ch] for k, v in a.items()}
    return a


def rx_view(a):
    """Вид «как видит srv»: время = приём; поздние записи (лаг > 2 с) и устаревшие по E отбрасываются."""
    o = np.argsort(a["rx"], kind="stable")
    a = {k: v[o] for k, v in a.items()}
    cm = np.maximum.accumulate(a["e"])
    ok = (a["lag"] <= 2000) & (a["e"] >= np.r_[cm[:1], cm[:-1]])
    a = {k: v[ok] for k, v in a.items()}
    a = dict(a)
    a["e"] = a["rx"]
    return a


def pair_series(A, B, t1):
    """Объединённый ряд по моментам изменения любой из двух сторон: (T, S, Bu, ia, ib). Только где есть обе стороны."""
    tA, tB = A["e"], B["e"]
    T = np.union1d(tA, tB)
    ia = np.searchsorted(tA, T, "right") - 1
    ib = np.searchsorted(tB, T, "right") - 1
    ok = (ia >= 0) & (ib >= 0) & (T < t1)
    T, ia, ib = T[ok], ia[ok], ib[ok]
    S = (A["bid"][ia] / B["ask"][ib] - 1.0) * 1e4
    Bu = (A["ask"][ia] / B["bid"][ib] - 1.0) * 1e4
    return T, S, Bu, ia, ib


def minute_bars(T, S, Bu):
    """Минутные бары как в viking (open/high/low/close для Sell и Buy). -> (минуты, n, S[4], B[4])."""
    m = T // 60000
    idx = np.flatnonzero(np.r_[True, m[1:] != m[:-1]])
    end = np.r_[idx[1:], len(T)]
    n = end - idx
    sb = np.stack([S[idx], np.maximum.reduceat(S, idx), np.minimum.reduceat(S, idx), S[end - 1]])
    bb = np.stack([Bu[idx], np.maximum.reduceat(Bu, idx), np.minimum.reduceat(Bu, idx), Bu[end - 1]])
    return m[idx], n, sb, bb


def rolling_centre(mins, sb, bb):
    """Центр по минуте k = медиана середины закрытия (S_c + B_c)/2 баров окна [k−60, k−1]; NaN, если баров < 30. -> dict минута→центр, m0, массив."""
    m0, m1 = int(mins[0]), int(mins[-1])
    mid = np.full(m1 - m0 + 1, np.nan)
    mid[mins - m0] = (sb[3] + bb[3]) / 2
    pad = np.r_[np.full(WIN_MIN, np.nan), mid]
    win = np.lib.stride_tricks.sliding_window_view(pad, WIN_MIN)[: len(mid)]   # окно [k−60, k−1] для минуты k
    cnt = np.sum(~np.isnan(win), axis=1)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        c = np.nanmedian(win, axis=1)
    c[cnt < NEED_BARS] = np.nan
    return m0, c


def whist(x, w):
    i = np.clip(((x - HIST_LO) / HIST_STEP).astype(np.int64), 0, HIST_N - 1)
    return np.bincount(i, weights=w, minlength=HIST_N)


def wquant(h, qs):
    c = np.cumsum(h)
    tot = c[-1]
    if tot <= 0:
        return [None] * len(qs)
    return [float(HIST_LO + (min(int(np.searchsorted(c, q * tot)), HIST_N - 1) + 0.5) * HIST_STEP) for q in qs]


def episodes(T, entry, exit_):
    """Эпизоды «вход → выход»: вход — первый момент entry после прошлого выхода, выход — первый момент exit_ после входа.
    -> [(i_вход, i_выход или -1)] (−1 — не вышли до конца данных)."""
    ei, xi = np.flatnonzero(entry), np.flatnonzero(exit_)
    out, k = [], 0
    while k < len(ei):
        i = int(ei[k])
        q = int(np.searchsorted(xi, i, "right"))
        if q >= len(xi):
            out.append((i, -1))
            break
        j = int(xi[q])
        out.append((i, j))
        k = int(np.searchsorted(ei, j, "right"))
    return out


def runs(T, mask, tend):
    """Подряд идущие интервалы, где mask истинно: [(начало, длительность мс, индекс начала, индекс конца включительно)]."""
    if not mask.any():
        return []
    d = np.diff(np.r_[False, mask, False].astype(np.int8))
    st, en = np.flatnonzero(d == 1), np.flatnonzero(d == -1) - 1
    Tn = np.r_[T, tend]
    return [(int(T[a]), int(Tn[b + 1] - T[a]), int(a), int(b)) for a, b in zip(st, en)]


def analyze_pair(A, B, t0, t1):
    """Полная статистика пары за окно [t0, t1) мс. Возвращает (строка, бары, список эпизодов) или None, если данных мало."""
    T, S, Bu, ia, ib = pair_series(A, B, t1)
    if len(T) < 50:
        return None
    depth = np.minimum(A["dep"][ia], B["dep"][ib])   # $ на лучшем уровне: меньшая из двух бирж
    mins, nb, sb, bb = minute_bars(T, S, Bu)
    m0, cen = rolling_centre(mins, sb, bb)
    c = cen[np.clip(T // 60000 - m0, 0, len(cen) - 1)]
    inw = T >= t0
    Tw, Sw, Bw, cw, depw = T[inw], S[inw], Bu[inw], c[inw], depth[inw]
    if len(Tw) < 50:
        return None
    dt = np.minimum(np.diff(np.r_[Tw, t1]), CAP_MS).astype(float)
    cover_s = float(dt.sum() / 1000)
    M = (Sw + Bw) / 2
    hS, hB, hM = whist(Sw, dt), whist(Bw, dt), whist(M, dt)
    pS, pB, pM = wquant(hS, (0.05, 0.5, 0.95)), wquant(hB, (0.05, 0.5, 0.95)), wquant(hM, (0.05, 0.5, 0.95))
    gap = wquant(whist(Bw - Sw, dt), (0.5, 0.95))
    row = {"n": int(len(Tw)), "cover_s": round(cover_s, 1), "Sq": pS, "Bq": pB, "M": pM, "gap": gap,
           "S_max": float(Sw.max()), "B_min": float(Bw.min())}
    # доля времени с исполнимым пересечением стаканов (Sell > 0 или Buy < 0) и длительность пересечений
    cr_s, cr_b = Sw > 0, Bw < 0
    row["cross_share"] = float(dt[cr_s | cr_b].sum() / dt.sum())
    rr = runs(Tw, cr_s, t1) + runs(Tw, cr_b, t1)
    rd = np.array([r[1] for r in rr], float)
    row["cross_n"] = len(rr)
    row["cross_dur_ms"] = [float(np.median(rd)), float(np.percentile(rd, 90)), float(rd.max())] if len(rr) else None
    # центр-зависимые величины — только где центр известен
    v = ~np.isnan(cw)
    row["valid_share"] = float(dt[v].sum() / dt.sum())
    res_eps, hDev = {}, None
    if v.sum() > 50:
        Tv, Sv, Bv, cv, dv, depv = Tw[v], Sw[v], Bw[v], cw[v], dt[v], depw[v]
        Es, Eb = Sv - cv, cv - Bv
        mx = np.maximum(Es, Eb)
        row["centre"] = float(np.average(cv, weights=dv))
        dev = np.abs((Sv + Bv) / 2 - cv)
        row["width_p90"] = _wq(dev, dv, 0.9)
        hDev = whist((Sv + Bv) / 2 - cv, dv)
        tot = dv.sum()
        row["share"] = [float(dv[mx >= th].sum() / tot) for th in LEVELS]
        row["edge_x"] = [float(dv[mx >= x].sum() / tot) for x in EDGE_X]
        row["edge_q"] = wquant(whist(mx, dv), (0.95, 0.99, 0.999))
        ep_out = []
        Mv = (Sv + Bv) / 2
        for li, th in enumerate(LEVELS):
            rows = []
            # вход: исполнимая цена на th bps выгоднее центра; выход (схождение): середина вернулась к центру
            for dirn, ent, ex, e in ((1, Es >= th, Mv <= cv, Es), (-1, Eb >= th, Mv >= cv, Eb)):
                for i, j in episodes(Tv, ent, ex):
                    jj = j if j >= 0 else len(Tv) - 1
                    tau = int(Tv[j] - Tv[i]) if j >= 0 else int(t1 - Tv[i])
                    gross = None
                    if j >= 0:   # круг: вход по исполнимой цене, выход исполнимой ценой в момент схождения (до комиссий)
                        gross = float(Sv[i] - Bv[j]) if dirn == 1 else float(Sv[j] - Bv[i])
                    rows.append((int(Tv[i]), dirn, tau, j < 0, float(e[i:jj + 1].max()), gross, float(depv[i])))
            ep_out.append(rows)
            res_eps[th] = rows
        row["eps"] = [_ep_summary(r) for r in ep_out]
    bars = {"m0": int(mins[0]), "mins": (mins - int(mins[0])).astype(int), "n": nb, "S": sb, "B": bb, "centre_m0": m0, "centre": cen}
    return row, bars, res_eps, {"hS": hS, "hB": hB, "hM": hM, "hDev": hDev}


def _wq(x, w, q):
    o = np.argsort(x)
    c = np.cumsum(w[o])
    return float(x[o][min(int(np.searchsorted(c, q * c[-1])), len(x) - 1)])


def _ep_summary(rows):
    """[число, вышло, p50, p90, max времени схождения (с) по вышедшим, не вышло, корзины времени, валовая раздвижка круга]."""
    conv = np.array([r[2] for r in rows if not r[3]], float)
    cens = sum(1 for r in rows if r[3])
    bins = np.bincount(np.searchsorted(TAU_EDGES_MS, conv, "right"), minlength=len(TAU_EDGES_MS) + 1).tolist() if len(conv) else [0] * (len(TAU_EDGES_MS) + 1)
    peak = float(np.median([r[4] for r in rows])) if rows else None
    gr = np.array([r[5] for r in rows if r[5] is not None], float)
    size = float(np.median([r[6] for r in rows])) if rows else None
    return {"size_med": size, "n": len(rows), "conv": int(len(conv)), "cens": cens,
            "tau_s": [float(np.median(conv) / 1000), float(np.percentile(conv, 90) / 1000), float(conv.max() / 1000)] if len(conv) else None,
            "bins": bins, "peak_med": peak,
            "gross_med": float(np.median(gr)) if len(gr) else None, "gross_ge_fee": int((gr >= FEE_RT_BPS).sum()), "gross_gt0": int((gr > 0).sum())}
