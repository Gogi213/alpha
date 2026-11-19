"""Выходы «через центр» и «переворот» для входов тейкер-тейкер (outlier.py) и мейкер + хедж (maker.py): идея владельца — войти при расхождении −θ
(лонг на дешёвой бирже, шорт на дорогой) и закрыть не у центра, а на противоположной стороне +θ_exit (прибыль ≈ θ + θ_exit при тех же комиссиях круга).

Правила выхода (ключ в строке настройки):
  «c-t» — к центру, тейкер (прежний, outlier.simulate_tt «полное» / maker.simulate «тт»; здесь не пересчитывается — вызывается как есть);
  «c-m» — к центру, мейкер-нога (прежний maker «мт» для мейкер-входа; для тейкер-входа — тот же закрывающий лимит на ноге M, хедж тейкером);
  «x1-t», «x2-t» — через центр: выход, когда ИСПОЛНИМАЯ цена противоположной стороны ушла от центра на θ_exit = θ (x1) или θ/2 (x2):
        закрытие позиции «продали A / купили B» (side +1) — Buy ≤ c − θ_exit, позиции side −1 — Sell ≥ c + θ_exit (сигнал = вход противоположной стороны с порогом θ_exit);
        решение по состоянию пары, исполнение — тейкером на обеих ногах через L мс по ценам того момента (как вход);
  «x1-m», «x2-m» — то же, но выход — лимитная заявка на ноге M с ценой, дающей запас выхода c ± θ_exit при хедже тейкером на H (как «мт» maker.py: заявка
        пересчитывается по состоянию на t − D, исполнение — сделка СТРОГО за ценой, хедж на H через L мс после исполнения); M — нога с большим числом сделок в окне;
  «flip» — переворот: при том же сигнале +θ позиция закрывается и сразу открывается обратная той же сделкой по тем же ценам, повтор (только тейкер-тейкер).
После выхода «через центр» новый вход разрешён только когда середина пары вернулась через центр (иначе тот же сигнал открыл бы тут же ту же позицию — это «переворот»).
Комиссия круга: тейкер-тейкер — 4 тейкерские ноги (12,6 bps), с мейкер-ногой выхода — 2 тейкера входа + мейкер + тейкер хеджа = 10,71 bps.
Строка круга — как maker.COLS (9 полей) + «tx» (вход исполнен, мс) и «te» (выход исполнен, мс).
"""
import numpy as np

import core
import maker
import outlier
from outlier import first_le

EPS = maker.EPS
FEE_TT4 = outlier.FEE_TT4
FEE_MX = maker.FEE_TT
COLS = maker.COLS + ("tx", "te")
NC = len(COLS)


def first_ge(x, k0, target):
    """Первый индекс k ≥ k0, где x[k] ≥ target (NaN — нет); −1, если нет."""
    n, lo, ch = len(x), k0, 2048
    while lo < n:
        w = np.flatnonzero(x[lo:lo + ch] >= target)
        if len(w):
            return lo + int(w[0])
        lo += ch
        ch = min(ch * 4, 1 << 22)
    return -1


def ext9(arr, lat, mk=False):
    """Круги прежнего вида (9 полей) -> 11 полей: tx = вход исполнен (тейкер-тейкер: сигнал + L; мейкер-вход: сделка на M), te = tx-независимо t + удержание."""
    arr = np.asarray(arr, float).reshape(-1, 9)
    tx = arr[:, 0] if mk else arr[:, 0] + lat
    return np.c_[arr, tx, arr[:, 0] + arr[:, 6]]


def exit_sig(P):
    """Сигнал выхода позиции стороны side: расхождение противоположной исполнимой цены от центра (bps)."""
    return {1: P["cT"] - P["Bu"], -1: P["S"] - P["cT"]}


def _next_entry(P, ent, t_free):
    T = P["T"]
    k = int(np.searchsorted(T, t_free, "left"))
    j1, j2 = int(np.searchsorted(ent[1], k)), int(np.searchsorted(ent[-1], k))
    i1 = int(ent[1][j1]) if j1 < len(ent[1]) else None
    i2 = int(ent[-1][j2]) if j2 < len(ent[-1]) else None
    if i1 is None and i2 is None:
        return None
    return (1, i1) if i2 is None or (i1 is not None and i1 <= i2) else (-1, i2)


def _centre_at(P, t):
    return P["cen"][min(max(t // 60000 - P["m0"], 0), len(P["cen"]) - 1)]


def _idx(book, t):
    return int(np.searchsorted(book["e"], t, "right")) - 1


def _enter(P, i, side, lat):
    """Вход тейкером на обеих ногах через L мс после сигнала по индексу i (как outlier.simulate_tt). -> (ts, tx, edge, slip, size, div) или None (вышли за окно)."""
    A, B, T = P["A"], P["B"], P["T"]
    ts = int(T[i])
    tx = ts + lat
    if tx >= P["t1"]:
        return None
    im, ib = _idx(A, tx), _idx(B, tx)
    sell = (A["bid"][im] / B["ask"][ib] - 1) * 1e4
    buy = (A["ask"][im] / B["bid"][ib] - 1) * 1e4
    if side > 0:
        edge, slip, size = sell, P["S"][i] - sell, min(A["dbid"][im], B["dask"][ib])
    else:
        edge, slip, size = buy, buy - P["Bu"][i], min(A["dask"][im], B["dbid"][ib])
    return ts, tx, edge, slip, size, side * (edge - _centre_at(P, tx))


def _gross_exit(P, side, edge, te):
    """Валовый итог круга при закрытии тейкером на обеих ногах по ценам момента te (bps)."""
    A, B = P["A"], P["B"]
    im, ib = _idx(A, te), _idx(B, te)
    if side > 0:
        return edge - (A["ask"][im] / B["bid"][ib] - 1) * 1e4
    return (A["bid"][im] / B["ask"][ib] - 1) * 1e4 - edge


def simulate_cross(P, ent, lat, th_exit, rearm=True):
    """Тейкер-тейкер вход, выход «через центр» тейкером. -> (круги (n, 11), не закрыто 0/1)."""
    T, t1 = P["T"], P["t1"]
    Z, X = exit_sig(P), {1: P["Dm"], -1: -P["Dm"]}
    out, unclosed, t_free = [], 0, P["t0"]
    while True:
        nxt = _next_entry(P, ent, t_free)
        if nxt is None:
            break
        side, i = nxt
        e = _enter(P, i, side, lat)
        if e is None:
            break
        ts, tx, edge, slip, size, div = e
        k0 = max(int(np.searchsorted(T, tx, "right")) - 1, 0)
        ke = first_ge(Z[side], k0, th_exit)
        te = max(int(T[ke]), tx) + lat if ke >= 0 else t1
        if te >= t1:
            out.append((ts, side, edge, slip, size, np.nan, np.nan, np.nan, div, tx, np.nan))
            unclosed = 1
            break
        gross = _gross_exit(P, side, edge, te)
        out.append((ts, side, edge, slip, size, gross - FEE_TT4, te - ts, gross, div, tx, te))
        t_free = te
        if rearm:
            kr = first_le(X[-side], ke, 0.0)
            if kr < 0:
                break
            t_free = max(te, int(T[kr]))
    return np.array(out, float).reshape(-1, NC), unclosed


def simulate_flip(P, ent, lat, th):
    """Тейкер-тейкер вход, «переворот»: закрытие по сигналу противоположной стороны с порогом θ и тут же вход в обратную сторону той же сделкой."""
    A, B, T, t1 = P["A"], P["B"], P["T"], P["t1"]
    Z = exit_sig(P)
    out, unclosed = [], 0
    nxt = _next_entry(P, ent, P["t0"])
    if nxt is None:
        return np.zeros((0, NC)), 0
    side, i = nxt
    e = _enter(P, i, side, lat)
    while e is not None:
        ts, tx, edge, slip, size, div = e
        k0 = max(int(np.searchsorted(T, tx, "right")) - 1, 0)
        ke = first_ge(Z[side], k0, th)
        te = max(int(T[ke]), tx) + lat if ke >= 0 else t1
        if te >= t1:
            out.append((ts, side, edge, slip, size, np.nan, np.nan, np.nan, div, tx, np.nan))
            unclosed = 1
            break
        im, ib = _idx(A, te), _idx(B, te)
        sell = (A["bid"][im] / B["ask"][ib] - 1) * 1e4
        buy = (A["ask"][im] / B["bid"][ib] - 1) * 1e4
        gross = edge - buy if side > 0 else sell - edge
        out.append((ts, side, edge, slip, size, gross - FEE_TT4, te - ts, gross, div, tx, te))
        # обратная позиция открыта той же сделкой по тем же ценам
        ns = -side
        if ns > 0:
            nedge, nslip, nsize = sell, P["S"][ke] - sell, min(A["dbid"][im], B["dask"][ib])
        else:
            nedge, nslip, nsize = buy, buy - P["Bu"][ke], min(A["dask"][im], B["dbid"][ib])
        ts2 = max(int(T[ke]), tx)
        e, side = (ts2, te, nedge, nslip, nsize, ns * (nedge - _centre_at(P, te))), ns
    return np.array(out, float).reshape(-1, NC), unclosed


# ---------- мейкер-нога выхода при входе тейкер-тейкер ----------

def prep_exit_mk(P, tr_e, tr_p, m_is_A, d_ms=maker.D_MS):
    """Всё для закрывающего лимита на ноге M (A или B) пары P: сделки M в окне, состояние пары на t − D. None — нет сделок."""
    A, B = P["A"], P["B"]
    w = (tr_e >= P["t0"]) & (tr_e < P["t1"])
    tr_e, tr_p = tr_e[w], tr_p[w]
    if not len(tr_e):
        return None
    M = A if m_is_A else B
    tq = tr_e - d_ms
    ia, ib = np.searchsorted(A["e"], tq, "right") - 1, np.searchsorted(B["e"], tq, "right") - 1
    cq = P["cen"][np.clip(tq // 60000 - P["m0"], 0, len(P["cen"]) - 1)]
    valid = (ia >= 0) & (ib >= 0) & ~np.isnan(cq)
    ia, ib = np.maximum(ia, 0), np.maximum(ib, 0)
    return {"m_is_A": m_is_A, "D": d_ms, "tr_e": tr_e, "tr_p": tr_p, "tick": maker.tick_of(M["bid"], M["ask"], tr_p), "valid": valid,
            "bA": A["bid"][ia], "aA": A["ask"][ia], "bB": B["bid"][ib], "aB": B["ask"][ib], "cq": cq, "cache": {}}


def close_cands(Q, x):
    """Закрывающие лимитки на M с запасом выхода c ∓ x (x = 0 — к центру): {сторона позиции: (время сделки, цена лимитки)}; сделка считается, если прошла СТРОГО за ценой."""
    if x in Q["cache"]:
        return Q["cache"][x]
    p, c, v, tick, te = Q["tr_p"], Q["cq"], Q["valid"], Q["tick"], Q["tr_e"]
    if Q["m_is_A"]:
        q1 = np.minimum(Q["bB"] * (1 + (c - x) / 1e4), Q["aA"] - tick)      # side +1 (продали A): покупка A лимиткой, хедж — продажа B по биду
        q2 = np.maximum(Q["aB"] * (1 + (c + x) / 1e4), Q["bA"] + tick)      # side −1 (купили A): продажа A лимиткой, хедж — покупка B по оферу
        f1, f2 = v & (p < q1 * (1 - EPS)), v & (p > q2 * (1 + EPS))
    else:
        q1 = np.maximum(Q["aA"] / (1 + (c - x) / 1e4), Q["bB"] + tick)      # side +1: продажа B лимиткой, хедж — покупка A по оферу
        q2 = np.minimum(Q["bA"] / (1 + (c + x) / 1e4), Q["aB"] - tick)      # side −1: покупка B лимиткой, хедж — продажа A по биду
        f1, f2 = v & (p > q1 * (1 + EPS)), v & (p < q2 * (1 - EPS))
    Q["cache"][x] = {1: (te[f1], q1[f1]), -1: (te[f2], q2[f2])}
    return Q["cache"][x]


def simulate_cross_mk(P, ent, lat, Q, x, rearm):
    """Тейкер-тейкер вход, выход — лимитка на ноге M (запас c ∓ x), хедж тейкером через L мс после исполнения. x = 0 — «к центру»."""
    A, B, T, t1 = P["A"], P["B"], P["T"], P["t1"]
    cc = close_cands(Q, x)
    X = {1: P["Dm"], -1: -P["Dm"]}
    out, unclosed, t_free = [], 0, P["t0"]
    while True:
        nxt = _next_entry(P, ent, t_free)
        if nxt is None:
            break
        side, i = nxt
        e = _enter(P, i, side, lat)
        if e is None:
            break
        ts, tx, edge, slip, size, div = e
        ce, cp = cc[side]
        j = int(np.searchsorted(ce, tx + Q["D"], "left"))
        te = gross = None
        if j < len(ce):
            t2, q = int(ce[j]), float(cp[j])
            te = t2 + lat
            if te < t1:
                ia, ib = _idx(A, te), _idx(B, te)
                if side > 0:
                    gross = edge - ((q / B["bid"][ib] if Q["m_is_A"] else A["ask"][ia] / q) - 1) * 1e4
                else:
                    gross = ((q / B["ask"][ib] if Q["m_is_A"] else A["bid"][ia] / q) - 1) * 1e4 - edge
        if gross is None:
            out.append((ts, side, edge, slip, size, np.nan, np.nan, np.nan, div, tx, np.nan))
            unclosed = 1
            break
        out.append((ts, side, edge, slip, size, gross - FEE_MX, te - ts, gross, div, tx, te))
        t_free = te
        if rearm:
            kr = first_le(X[-side], max(int(np.searchsorted(T, te, "right")) - 1, 0), 0.0)
            if kr < 0:
                break
            t_free = max(te, int(T[kr]))
    return np.array(out, float).reshape(-1, NC), unclosed


# ---------- мейкер-вход (maker.py) с выходом «через центр» ----------

def prep_mk_x(M, H, tr_e, tr_p, t0, t1, d_ms=maker.D_MS):
    """maker.prep + ряды пары (S, Bu, центр, отклонение середины) для выходов «через центр». None — как у maker.prep."""
    P = maker.prep(M, H, tr_e, tr_p, t0, t1, d_ms)
    if P is None:
        return None
    T, S, Bu, _, _ = core.pair_series(M, H, t1, stale_ms=None)
    mins, _, sb, bb = core.minute_bars(T, S, Bu)
    m0, cen = core.rolling_centre(mins, sb, bb)
    cT = cen[np.clip(T // 60000 - m0, 0, len(cen) - 1)]
    P.update(S=S, Bu=Bu, cT=cT, Dm=(S + Bu) / 2 - cT, m0=m0, cen=cen, zidx={}, xclose={})
    return P


def zidx(P, x):
    """Индексы ряда T, где исполнимое расхождение к центру противоположной стороны ≥ x: {side открытой позиции: индексы} (side +1 = продали на M)."""
    if x not in P["zidx"]:
        Z = exit_sig(P)
        P["zidx"][x] = {s: np.flatnonzero(Z[s] >= x) for s in (1, -1)}
    return P["zidx"][x]


def close_cands_mk(P, x):
    """Закрывающие лимитки на M (maker.prep, но с запасом c ∓ x): {сторона открытой позиции: (время сделки, цена)}."""
    if x == 0:
        return P["close"]
    if x not in P["xclose"]:
        p, c, v, tick = P["tr_p"], P["cq"], P["valid"], P["tick"]
        qcb = np.minimum(P["bH"] * (1 + (c - x) / 1e4), P["aM"] - tick)
        qcs = np.maximum(P["aH"] * (1 + (c + x) / 1e4), P["bM"] + tick)
        fb, fs = v & (p < qcb * (1 - EPS)), v & (p > qcs * (1 + EPS))
        P["xclose"][x] = {1: (P["tr_e"][fb], qcb[fb]), -1: (P["tr_e"][fs], qcs[fs])}
    return P["xclose"][x]


def simulate_mk_x(P, cand, lat, variant, x, rearm):
    """maker.simulate с выходом «через центр» запаса x: variant «tt» — тейкер на обеих ногах по сигналу, «mt» — лимитка на M. x = 0 — то же, что maker.simulate.
    -> (круги (n, 11), не закрыто)."""
    M, H, T, t1 = P["M"], P["H"], P["T"], P["t1"]
    ct, cs, cq, cref, cc = cand
    X = {1: P["Dm"], -1: -P["Dm"]}
    out, free, unclosed = [], P["t0"], 0
    while True:
        k = int(np.searchsorted(ct, free, "left"))
        if k >= len(ct):
            break
        tf, side, q = int(ct[k]), int(cs[k]), float(cq[k])
        th = tf + lat
        if th >= t1:
            break
        ih = _idx(H, th)
        if side > 0:
            hp, top = H["ask"][ih], H["dask"][ih]
            slip = (hp / cref[k] - 1) * 1e4
        else:
            hp, top = H["bid"][ih], H["dbid"][ih]
            slip = (cref[k] / hp - 1) * 1e4
        edge = (q / hp - 1) * 1e4
        div = side * (edge - float(cc[k]))
        gross, flat, fee, ke = None, None, None, -1
        if variant == "tt":
            idx = P["idx"][side] if x == 0 else zidx(P, x)[side]
            j = int(np.searchsorted(idx, max(int(np.searchsorted(T, th, "right")) - 1, 0)))
            if j < len(idx):
                ke = int(idx[j])
                tc = max(int(T[ke]), th) + lat
                if tc < t1:
                    im, ih2 = _idx(M, tc), _idx(H, tc)
                    gross = edge - (M["ask"][im] / H["bid"][ih2] - 1) * 1e4 if side > 0 else (M["bid"][im] / H["ask"][ih2] - 1) * 1e4 - edge
                    flat, fee = tc, maker.FEE_TT
        else:
            ce, cp = close_cands_mk(P, x)[side]
            j = int(np.searchsorted(ce, th + P["D"], "left"))
            if j < len(ce):
                th2 = int(ce[j]) + lat
                if th2 < t1:
                    ih2 = _idx(H, th2)
                    gross = edge - (cp[j] / H["bid"][ih2] - 1) * 1e4 if side > 0 else (cp[j] / H["ask"][ih2] - 1) * 1e4 - edge
                    flat, fee = th2, maker.FEE_MT
        if gross is None:
            out.append((tf, side, edge, slip, float(top), np.nan, np.nan, np.nan, div, tf, np.nan))
            unclosed = 1
            break
        out.append((tf, side, edge, slip, float(top), gross - fee, flat - tf, gross, div, tf, flat))
        free = flat + P["D"]
        if rearm:
            kr = first_le(X[-side], max(int(np.searchsorted(T, flat, "right")) - 1, 0), 0.0)
            if kr < 0:
                break
            free = max(free, int(T[kr]))
    return np.array(out, float).reshape(-1, NC), unclosed


# ---------- фандинг за время удержания ----------

def funding_cum(rows):
    """[[время_мс, ставка], …] -> (времена, накопленная сумма ставок с нулём в начале)."""
    a = np.asarray(rows, float).reshape(-1, 2)
    return a[:, 0].astype(np.int64), np.r_[0.0, np.cumsum(a[:, 1])]


def funding_bps(arr, f_first, f_second):
    """Фандинг круга в bps номинала (+ получено): позиция side +1 — шорт на первой бирже и лонг на второй, side −1 — наоборот; выплата — на отметках в (tx, te].
    f_first / f_second — funding_cum() первой и второй биржи или None (нет истории → NaN)."""
    n = len(arr)
    out = np.full(n, np.nan)
    if f_first is None or f_second is None or not n:
        return out
    tx, te, side = arr[:, 9], arr[:, 10], arr[:, 1]
    ok = ~np.isnan(te)

    def over(f):
        t, c = f
        return c[np.searchsorted(t, np.where(ok, te, 0), "right")] - c[np.searchsorted(t, tx, "right")]

    d = over(f_first) - over(f_second)          # ставки: платит лонг, получает шорт; шорт на первой → получено (первая − вторая)
    out[ok] = (np.where(side > 0, d, -d) * 1e4)[ok]
    return out
