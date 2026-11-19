"""Торговля только выбросами: вход, когда расхождение к центру ≥ θ. Режим «тейкер-тейкер» на неупорядоченной паре бирж (A, B);
режим «мейкер + хедж» — maker.py с той же сеткой θ.

Тейкер-тейкер (формулы Viking, как вкладка «Арбитраж»): Sell = (bid_A / ask_B − 1)·10⁴ bps, Buy = (ask_A / bid_B − 1)·10⁴; центр c — авто-центр
(core.rolling_centre). Сигнал входа в продажу A / покупку B: Sell − c ≥ θ; в покупку A / продажу B: c − Buy ≥ θ (исполнимые цены на обеих ногах).
Вход исполняется тейкером на обеих ногах через L мс после сигнала по ценам на тот момент (рыночные заявки: вход происходит, даже если запас успел
уменьшиться; слёт запаса — «проскальзывание»). Закрытие: «полное» — середина пары вернулась к центру; «половина» — отклонение середины в сторону
входа упало вдвое от значения на входе. Решение о закрытии — по состоянию пары, исполнение — тейкером через L мс. Комиссия — 4 тейкерские ноги.
Одна позиция на пару; следующий вход — после закрытия. Не закрытый до конца окна круг — «не закрыт» (в доходах не участвует).
Размер на входе — меньший из объёмов лучших уровней обеих ног (по записанной книге: объём на момент последнего изменения цены).
Время — E биржи (книги), как вкладка.
"""
import numpy as np

import core
from maker import TAKER_BPS

FEE_TT4 = 4 * TAKER_BPS     # 12,6 bps
THETAS = (20.0, 30.0, 50.0, 75.0, 100.0)   # bps = 0,2 / 0,3 / 0,5 / 0,75 / 1,0 %
LATS_TT = (0, 50, 300)
CLOSES = ("full", "half")


def prep_tt(A, B, t0, t1):
    """Ряд пары (A, B) и центр. A, B — книги (dict e, bid, ask, dbid, dask; core.clean)."""
    T, S, Bu, ia, ib = core.pair_series(A, B, t1, stale_ms=None)
    if len(T) < 50:
        return None
    mins, _, sb, bb = core.minute_bars(T, S, Bu)
    m0, cen = core.rolling_centre(mins, sb, bb)
    cT = cen[np.clip(T // 60000 - m0, 0, len(cen) - 1)]
    return {"A": A, "B": B, "T": T, "S": S, "Bu": Bu, "cT": cT, "Dm": (S + Bu) / 2 - cT, "m0": m0, "cen": cen, "t0": t0, "t1": t1}


def entries_for(P, theta):
    """Индексы ряда T, где расхождение к центру ≥ θ: {+1: продажа A/покупка B, −1: покупка A/продажа B} (только внутри окна)."""
    inw = P["T"] >= P["t0"]
    return {1: np.flatnonzero(inw & (P["S"] - P["cT"] >= theta)), -1: np.flatnonzero(inw & (P["cT"] - P["Bu"] >= theta))}


def first_le(x, k0, target):
    """Первый индекс k ≥ k0, где x[k] ≤ target (NaN — нет); −1, если нет."""
    n, lo, ch = len(x), k0, 2048
    while lo < n:
        w = np.flatnonzero(x[lo:lo + ch] <= target)
        if len(w):
            return lo + int(w[0])
        lo += ch
        ch = min(ch * 4, 1 << 22)
    return -1


def simulate_tt(P, ent, lat, close):
    """Круги одной настройки. -> массив (n, 9) по maker.COLS и число незакрытых (0/1)."""
    A, B, T, t0, t1 = P["A"], P["B"], P["T"], P["t0"], P["t1"]
    out, unclosed, t_free = [], 0, t0
    X = {1: P["Dm"], -1: -P["Dm"]}       # отклонение середины в сторону входа
    while True:
        k = int(np.searchsorted(T, t_free, "left"))
        j1, j2 = int(np.searchsorted(ent[1], k)), int(np.searchsorted(ent[-1], k))
        i1 = int(ent[1][j1]) if j1 < len(ent[1]) else None
        i2 = int(ent[-1][j2]) if j2 < len(ent[-1]) else None
        if i1 is None and i2 is None:
            break
        side, i = (1, i1) if i2 is None or (i1 is not None and i1 <= i2) else (-1, i2)
        ts = int(T[i])
        tx = ts + lat
        if tx >= t1:
            break
        im, ib = int(np.searchsorted(A["e"], tx, "right")) - 1, int(np.searchsorted(B["e"], tx, "right")) - 1
        sell = (A["bid"][im] / B["ask"][ib] - 1) * 1e4
        buy = (A["ask"][im] / B["bid"][ib] - 1) * 1e4
        if side > 0:
            edge, sig, size = sell, P["S"][i], min(A["dbid"][im], B["dask"][ib])
            slip = sig - edge
        else:
            edge, sig, size = buy, P["Bu"][i], min(A["dask"][im], B["dbid"][ib])
            slip = edge - sig
        cx = P["cen"][min(max(tx // 60000 - P["m0"], 0), len(P["cen"]) - 1)]
        div = side * (edge - cx)
        x0 = side * ((sell + buy) / 2 - cx)
        target = 0.0 if close == "full" or not x0 > 0 else x0 / 2
        k0 = max(int(np.searchsorted(T, tx, "right")) - 1, 0)
        ke = first_le(X[side], k0, target)
        gross = None
        if ke >= 0:
            te = max(int(T[ke]), tx) + lat
            if te < t1:
                im2, ib2 = int(np.searchsorted(A["e"], te, "right")) - 1, int(np.searchsorted(B["e"], te, "right")) - 1
                gross = edge - (A["ask"][im2] / B["bid"][ib2] - 1) * 1e4 if side > 0 else (A["bid"][im2] / B["ask"][ib2] - 1) * 1e4 - edge
        if gross is None:
            out.append((ts, side, edge, slip, size, np.nan, np.nan, np.nan, div))
            unclosed = 1
            break
        out.append((ts, side, edge, slip, size, gross - FEE_TT4, te - ts, gross, div))
        t_free = te
    return np.array(out, float).reshape(-1, 9), unclosed
