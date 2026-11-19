"""Модель «мейкер-вход + тейкер-хедж» для упорядоченной пары бирж (M — главная нога с лимиткой, H — хедж) на записанных данных.

Как торгует робот Viking (strategies/xarb_base.md: Quote + Only maker + Simply first; хедж — тейкер второй ногой после сделки по главной):
  Sell = (bid_M / ask_H − 1)·10⁴ bps — продажа на M лимиткой q, покупка на H по оферу; Buy = (ask_M / bid_H − 1)·10⁴ — наоборот.
  Центр c — авто-центр viking (core.rolling_centre: медиана середин закрытий минутных баров за прошлые 60 мин, ≥ 30 баров).
Допущения модели (всё это — допущения, не измерения):
  • Котировка. Пока позиции нет, на M стоят две лимитки: на продажу q_s = max(ask_H·(1 + (c + θ)/10⁴), bid_M + tick) и на покупку
    q_b = min(bid_H·(1 + (c − θ)/10⁴), ask_M − tick) — цена, при которой хедж по текущей цене H даёт запас входа ≥ θ bps к центру;
    «bid_M + tick / ask_M − tick» — post-only: котировка не пересекает стакан M (Simply first). Цена пересчитывается по состоянию
    H, M и центра на момент t − D, D = 300 мс (период обновления Viking API, ответ на смену цены H).
  • Исполнение. Только когда на M проходит сделка СТРОГО за ценой лимитки (p > q_s или p < q_b); касание, очередь и частичные
    исполнения не засчитываются. Заявка при этом исполняется целиком по своей цене q (приоритет цены).
  • Хедж. Тейкер на H по лучшей цене H в момент t_fill + L (L ∈ {50, 300} мс — допущение о задержке бота). Размер круга — 100 USDT, не больше
    объёма лучшего уровня H (по записанной книге: объём на момент последнего изменения цены — повторы цены парсер не пишет).
  • Закрытие. «тт»: тейкер на обеих ногах по ценам момента сходимости + L (сходимость — середина пары вернулась к центру: viking-эпизод).
    «мт»: после хеджа (+D) стоит закрывающая лимитка на M с ценой, при которой хедж на H даёт запас выхода = центр (q_c = bid_H·(1 + c/10⁴)
    для покупки, ask_H·(1 + c/10⁴) для продажи), исполнение — той же строгой сделкой; хедж закрытия — тейкером на H в t + L.
  • Одна открытая позиция на упорядоченную пару и монету; следующая котировка — через D после закрытия.
  • Комиссии (тариф владельца −10 %): мейкер 1,4, тейкер 3,5 bps на ногу. «тт»: мейкер-вход + 3 тейкер-ноги = 10,71 bps; «мт»: 2·(мейкер + тейкер) = 8,82.
Время везде — E биржи (как вкладка «Арбитраж»). Круг, не вышедший до конца данных, — «не закрыт» (в доходах не участвует).
"""
import numpy as np

import core

D_MS = 300
MAKER_BPS = 1.4 * 0.9
TAKER_BPS = 3.5 * 0.9
FEE_TT = MAKER_BPS + 3 * TAKER_BPS      # 10,71
FEE_MT = 2 * (MAKER_BPS + TAKER_BPS)    # 8,82
THETAS = (round(FEE_TT, 2), round(FEE_TT + 5, 2), round(FEE_TT + 10, 2))   # «только комиссии», +5, +10 bps
LATS = (50, 300)
VARIANTS = ("tt", "mt")
SIZE_USD = 100.0
EPS = 1e-9
PERIODS_H = (1, 4, 6, 24)
COLS = ("t", "side", "edge", "slip", "size", "net", "hold", "gross", "div")   # t — время сделки на M (мс), side +1 — продали на M


def tick_of(*arrs):
    """Шаг цены: наименьшая положительная разность между различными ценами символа (по записанным ценам)."""
    u = np.unique(np.round(np.concatenate(arrs), 12))
    d = np.diff(u)
    d = d[d > 0]
    return float(d.min()) if len(d) else 0.0


def prep(M, H, tr_e, tr_p, t0, t1, d_ms=D_MS):
    """Всё, что не зависит от θ, L и варианта. M, H — книги (dict e, bid, ask, dbid, dask; core.clean), tr_e/tr_p — сделки M (по E).
    -> dict или None (нет пары, центра или сделок)."""
    T, S, Bu, ia, ib = core.pair_series(M, H, t1, stale_ms=None)
    if len(T) < 50:
        return None
    mins, _, sb, bb = core.minute_bars(T, S, Bu)
    m0, cen = core.rolling_centre(mins, sb, bb)
    cT = cen[np.clip(T // 60000 - m0, 0, len(cen) - 1)]
    Mv = (S + Bu) / 2
    ok = ~np.isnan(cT)
    P = {"M": M, "H": H, "T": T, "idx": {1: np.flatnonzero(ok & (Mv <= cT)), -1: np.flatnonzero(ok & (Mv >= cT))}, "t0": t0, "t1": t1, "D": d_ms}
    w = (tr_e >= t0) & (tr_e < t1)
    tr_e, tr_p = tr_e[w], tr_p[w]
    if not len(tr_e):
        return None
    tick = tick_of(M["bid"], M["ask"], tr_p)
    tq = tr_e - d_ms
    im, ih = np.searchsorted(M["e"], tq, "right") - 1, np.searchsorted(H["e"], tq, "right") - 1
    cq = cen[np.clip(tq // 60000 - m0, 0, len(cen) - 1)]
    valid = (im >= 0) & (ih >= 0) & ~np.isnan(cq)
    im, ih = np.maximum(im, 0), np.maximum(ih, 0)
    bM, aM, bH, aH = M["bid"][im], M["ask"][im], H["bid"][ih], H["ask"][ih]
    P.update(tr_e=tr_e, tr_p=tr_p, tick=tick, valid=valid, bM=bM, aM=aM, bH=bH, aH=aH, cq=cq)
    # закрывающие лимитки (для варианта «мт»): не зависят от θ
    qcb = np.minimum(bH * (1 + cq / 1e4), aM - tick)     # закрыть шорт на M: покупка
    qcs = np.maximum(aH * (1 + cq / 1e4), bM + tick)     # закрыть лонг на M: продажа
    fb, fs = valid & (tr_p < qcb * (1 - EPS)), valid & (tr_p > qcs * (1 + EPS))
    P["close"] = {1: (tr_e[fb], qcb[fb]), -1: (tr_e[fs], qcs[fs])}   # ключ — сторона ОТКРЫТОЙ позиции (+1: шорт на M)
    return P


def candidates(P, theta):
    """Сделки на M, дающие мейкер-вход при пороге θ: (время, сторона, цена лимитки, цена H в момент котировки)."""
    p, c = P["tr_p"], P["cq"]
    qs = np.maximum(P["aH"] * (1 + (c + theta) / 1e4), P["bM"] + P["tick"])
    qb = np.minimum(P["bH"] * (1 + (c - theta) / 1e4), P["aM"] - P["tick"])
    fs, fb = P["valid"] & (p > qs * (1 + EPS)), P["valid"] & (p < qb * (1 - EPS))
    k = np.flatnonzero(fs | fb)
    s = np.where(fs[k], 1, -1)
    return P["tr_e"][k], s, np.where(fs[k], qs[k], qb[k]), np.where(fs[k], P["aH"][k], P["bH"][k]), c[k]


def simulate(P, cand, lat, variant):
    """Круги одной настройки. -> массив (n, 7) по COLS и число незакрытых (0/1: после него позиция висит до конца данных)."""
    M, H, T, t0, t1 = P["M"], P["H"], P["T"], P["t0"], P["t1"]
    ct, cs, cq, cref, cc = cand
    out = []
    free = t0
    unclosed = 0
    while True:
        k = int(np.searchsorted(ct, free, "left"))
        if k >= len(ct):
            break
        tf, side, q = int(ct[k]), int(cs[k]), float(cq[k])
        th = tf + lat
        if th >= t1:
            break
        ih = int(np.searchsorted(H["e"], th, "right")) - 1
        if side > 0:    # продали на M, купили на H по оферу
            hp, top = H["ask"][ih], H["dask"][ih]
            slip = (hp / cref[k] - 1) * 1e4
        else:
            hp, top = H["bid"][ih], H["dbid"][ih]
            slip = (cref[k] / hp - 1) * 1e4
        edge = (q / hp - 1) * 1e4
        div = side * (edge - float(cc[k]))     # расхождение входа к центру, bps (в сторону входа)
        size = float(top)                      # полный объём лучшего уровня H, $ (потолок 100 USDT — при подсчёте)
        gross, flat = None, None
        if variant == "tt":
            idx = P["idx"][side]
            j = int(np.searchsorted(idx, max(int(np.searchsorted(T, th, "right")) - 1, 0)))
            if j < len(idx):
                tc = max(int(T[idx[j]]), th) + lat
                if tc < t1:
                    im, ih2 = int(np.searchsorted(M["e"], tc, "right")) - 1, int(np.searchsorted(H["e"], tc, "right")) - 1
                    if side > 0:
                        gross = edge - (M["ask"][im] / H["bid"][ih2] - 1) * 1e4
                    else:
                        gross = (M["bid"][im] / H["ask"][ih2] - 1) * 1e4 - edge
                    flat, fee = tc, FEE_TT
        else:
            ce, cp = P["close"][side]
            j = int(np.searchsorted(ce, th + P["D"], "left"))
            if j < len(ce):
                t2 = int(ce[j])
                th2 = t2 + lat
                if th2 < t1:
                    ih2 = int(np.searchsorted(H["e"], th2, "right")) - 1
                    if side > 0:
                        gross = edge - (cp[j] / H["bid"][ih2] - 1) * 1e4
                    else:
                        gross = (cp[j] / H["ask"][ih2] - 1) * 1e4 - edge
                    flat, fee = th2, FEE_MT
        if gross is None:
            out.append((tf, side, edge, slip, size, np.nan, np.nan, np.nan, div))
            unclosed = 1
            break
        out.append((tf, side, edge, slip, size, gross - fee, flat - tf, gross, div))
        free = flat + P["D"]
    return np.array(out, float).reshape(-1, len(COLS)), unclosed


def pq(x, q):
    return float(np.percentile(x, q)) if len(x) else None


def summarize(r, unclosed, t0, t1):
    """Сводка кругов одной настройки: число входов за 1/4/6/24 ч, чистый итог круга, проскальзывание хеджа, доля >0, $ в сутки."""
    t = r[:, 0]
    cl = r[~np.isnan(r[:, 5])]
    win_h = (t1 - t0) / 3600_000
    net, size = cl[:, 5], np.minimum(cl[:, 4], SIZE_USD)
    return {"n": int(len(r)), "closed": int(len(cl)), "unclosed": int(unclosed),
            "fills": [int((t >= t1 - h * 3600_000).sum()) for h in PERIODS_H],
            "net": [pq(net, 50), pq(net, 95)], "pos": float((net > 0).mean()) if len(net) else None,
            "slip": [pq(r[:, 3], 50), pq(r[:, 3], 95)], "hold_s": [pq(cl[:, 6] / 1000, 50), pq(cl[:, 6] / 1000, 95)],
            "usd_day": float((size * net / 1e4).sum() / (win_h / 24)), "edge_med": pq(r[:, 2], 50),
            "size_med": pq(r[:, 4], 50)}


def rounds_for(P, thetas, lats, variants=VARIANTS):
    """Круги по сетке (θ, L, вариант) без сводок: {(θ, L, вариант): (массив кругов, не закрыто)}."""
    out = {}
    for th in thetas:
        cand = candidates(P, th)
        for lat in lats:
            for var in variants:
                out[(th, lat, var)] = simulate(P, cand, lat, var)
    return out


def run_pair(P):
    """Все настройки одной пары: {(индекс θ, L, вариант): (круги, сводка)}."""
    out = {}
    for ti, th in enumerate(THETAS):
        cand = candidates(P, th)
        for lat in LATS:
            for var in VARIANTS:
                r, un = simulate(P, cand, lat, var)
                out[(ti, lat, var)] = (r, summarize(r, un, P["t0"], P["t1"]))
    return out
