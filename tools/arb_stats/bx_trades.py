"""Сделки BingX как событийная цена BingX: ядро счёта (G-022, 15.11.2026). Время — биржи (E книг, T сделок), мс.

Допущение, которое проверяется счётом (bx_snap_check.py): сделка с агрессором-покупателем по цене p ⇒ в этот миг ask = p (первая сделка пачки; последняя — нижняя граница ask после пачки);
агрессор-продавец ⇒ bid = p. BingX: m = true — «покупатель мейкер» ⇒ агрессор-продавец (docs/findings/bingx-ws-prep-2026-11-12.md, п. 3).
Пачка = сделки символа с одним временем T (мс) и одной стороной (одна рыночная заявка или несколько в ту же миллисекунду).
Формулы раздвижки — viking xarb_base §2: Sell = (bid_A / ask_B − 1)·10⁴ bps, Buy = (ask_A / bid_B − 1)·10⁴, A = быстрая биржа F, B = BingX. Цена BingX — из пачки:
  агрессор-покупатель (ask_B = p): Sell_t = (bid_F / p − 1)·10⁴ — «BingX дешевле»: купить на BingX по p, продать на F по биду;
  агрессор-продавец (bid_B = p): Buy_t = (ask_F / p − 1)·10⁴; выгода «продать на BingX по p, купить на F по оферу» = −Buy_t («BingX дороже»).
Режим «к центру» (c — авто-центр viking, core.rolling_centre по снимкам пары): вход вверх Sell − c ≥ θ, вход вниз c − Buy ≥ θ; режим «от нуля»: c = 0.
"""
import glob
from pathlib import Path

import numpy as np

import core

TZ_MS = 4 * 3600_000
DAY_MS = 86_400_000
TAUS_MS = (100, 1000, 10_000, 60_000, 600_000)       # возраст последней сделки, для доли времени «оценка старше τ»
LL_REF_MS = 1000       # движение быстрой биржи: середина изменилась на ≥ x за 1 с
LL_PERSIST_MS = 1000   # и через 1 с остаётся на той же стороне (не мерцание спреда)
LL_REFRAC_MS = 5000    # пауза после события
HORIZON_MS = 600_000   # ждём ответа BingX 10 мин
EP_GAP_MS = 5000       # эпизод: подходящие пачки подряд с паузой ≤ 5 с — один эпизод
GAP_LO, GAP_N = -500, 1001   # гистограмма раздвижки, шаг 1 bps на [−500; 500]
DUR_EDGES = np.r_[0, 10.0 ** np.arange(0, 6.01, 0.25)]   # мс: 0, 1 мс … 10⁶ мс (16,7 мин), шаг 10^0,25
GAP_EDGES = np.r_[0, 10.0 ** np.arange(0, 6.001, 0.02)]   # пауза между пачками, мс: шаг 10^0,02 (≈ 5 %)
THETAS = (20.0, 30.0, 50.0, 100.0)                         # bps = 0,2 / 0,3 / 0,5 / 1 %
XS = (0.002, 0.005)                                        # движение быстрой биржи ≥ 0,2 / 0,5 %


def day_edges(t0, t1):
    """Границы суток GMT+4 внутри окна: [t0, b1, b2, …, t1]."""
    first = (t0 + TZ_MS) // DAY_MS + 1
    last = (t1 - 1 + TZ_MS) // DAY_MS
    return [t0] + [d * DAY_MS - TZ_MS for d in range(first, last + 1)] + [t1]


def day_idx(t, edges):
    return np.clip(np.searchsorted(edges, t, "right") - 1, 0, len(edges) - 2)


def load_trades(work, sym, mult, t0, t1):
    """Сделки BingX из разобранных кусков (bx_trades_parse.py), цены / mult; None — сделок нет."""
    fs = sorted(glob.glob(str(Path(work, "bingx", sym + ".*.npz"))))
    if not fs:
        return None
    parts = [np.load(f) for f in fs]
    a = {k: np.concatenate([p[k] for p in parts]) for k in ("e", "et", "rx", "p", "q", "m")}
    T = a["e"] - a["et"]
    o = np.flatnonzero((T >= t0) & (T < t1) & (a["p"] > 0))
    if not len(o):
        return None
    o = o[np.lexsort((a["rx"][o], T[o]))]
    return {"T": T[o], "p": a["p"][o] / mult, "praw": a["p"][o], "q": a["q"][o], "m": a["m"][o]}


def make_batches(tr):
    """Пачки: время, сторона (+1 агрессор-покупатель, −1 агрессор-продавец), цена первой и последней сделки, min/max, число сделок, объём (USDT)."""
    T, p, m = tr["T"], tr["p"], tr["m"]
    o = np.lexsort((m, T))
    T, p, m, pr, q = T[o], p[o], m[o], tr["praw"][o], tr["q"][o]
    idx = np.flatnonzero(np.r_[True, (T[1:] != T[:-1]) | (m[1:] != m[:-1])])
    lo, hi = np.minimum.reduceat(p, idx), np.maximum.reduceat(p, idx)
    side = np.where(m[idx] == 1, -1, 1).astype(np.int8)
    return {"t": T[idx], "side": side, "first": np.where(side > 0, lo, hi), "last": np.where(side > 0, hi, lo), "lo": lo, "hi": hi,
            "n": np.diff(np.r_[idx, len(T)]), "usd": np.add.reduceat(pr * q, idx)}


def freq_stats(B, n_trades, t0, t1):
    """Как часто BingX печатает сделки у монеты: медиана и p90 паузы между пачками (разные моменты), доля окна, где последняя сделка старше τ,
    гистограмма пауз (DUR_EDGES) и доля пауз в ±10 % от медианной («метроном»: печать по таймеру, а не по событию)."""
    tt = np.unique(B["t"])
    gaps = np.diff(tt)
    g = np.diff(np.r_[t0, tt, t1])
    med = float(np.median(gaps)) if len(gaps) else None
    return {"n_trades": int(n_trades), "n_batches": int(len(B["t"])), "n_times": int(len(tt)),
            "gap_med": med, "gap_p90": float(np.percentile(gaps, 90)) if len(gaps) else None,
            "reg10": float(((gaps >= 0.9 * med) & (gaps <= 1.1 * med)).mean()) if len(gaps) else None,
            "gap_hist": np.histogram(gaps, GAP_EDGES)[0].astype(np.int64) if len(gaps) else np.zeros(len(GAP_EDGES) - 1, np.int64),
            "stale": [float(np.maximum(g - tau, 0).sum() / (t1 - t0)) for tau in TAUS_MS],
            "buy_share": float((B["side"] > 0).mean()), "usd_med": float(np.median(B["usd"])), "usd_sum": float(B["usd"].sum()),
            "n_per_batch": float(n_trades / max(len(B["t"]), 1))}


def first_where(x, k0, op, thr, end=None):
    """Первый индекс k в [k0, end), где op(x[k], thr); −1, если нет."""
    n = len(x) if end is None else min(end, len(x))
    lo, ch = k0, 1024
    while lo < n:
        w = np.flatnonzero(op(x[lo:min(lo + ch, n)], thr))
        if len(w):
            return lo + int(w[0])
        lo += ch
        ch = min(ch * 4, 1 << 22)
    return -1


def merge_dead(dead, exs, margin_ms=120_000):
    """Пропуски сбора нескольких бирж (dead.json, gaps_scan.py) с запасом после каждого, объединённые: (начала, концы) массивами или None."""
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


def dead_hit(t, dead):
    """Истина там, где момент t внутри пропуска (dead — merge_dead; запас после пропуска уже в нём)."""
    t = np.asarray(t)
    if dead is None:
        return np.zeros(t.shape, bool)
    ds, de = dead
    i = np.searchsorted(ds, t, "right") - 1
    return (i >= 0) & (t <= de[np.maximum(i, 0)])


def dead_overlap(ts, te, dead):
    """Истина, если [ts, te] пересекает пропуск."""
    if dead is None:
        return False
    ds, de = dead
    i = int(np.searchsorted(de, ts, "left"))
    return i < len(ds) and ds[i] <= te


# ---------- движение быстрой биржи → когда BingX печатает сделки на новом уровне ----------

def lead_lag(F, X, B, G, t0, t1, xs=XS, dead=None):
    """События «середина F сместилась на ≥ x за 1 с и держится через 1 с» и ответ BingX (базис = цена BingX / середина F − 1, середина F — на момент самой цены).
    Опора — базис до события: у снимков — на момент tc − 1 с; у сделок — медиана базиса последних ≤ 3 пачек той же стороны за 10 мин до tc − 1 с (нет — сделочного ответа нет).
    Движение m = |L1/L0 − 1|, порог = m/2. «Ответ» — первая запись/пачка после tc, чей базис вернулся в полосу опоры ± m/2 (BingX догнал F; прежняя цена даёт отклонение −d·m,
    d = направление движения). «led» — до события (за 1 с) уже была запись/пачка с базисом на ≥ m/2 в сторону движения (BingX опередил F).
    -> {x: массив (n, 9): [t, d, led, лаг сделки (мс, NaN — не было за 10 мин), лаг снимка, спред BingX/середина, m, число пачек за 10 мин после, есть опора для сделок]}."""
    fe, fm = F["e"], (F["bid"] + F["ask"]) / 2
    xe = X["e"]
    ixf = np.searchsorted(fe, xe, "right") - 1
    bsx = np.where(ixf >= 0, (X["bid"] + X["ask"]) / 2 / fm[np.maximum(ixf, 0)] - 1, np.nan)
    bt, bside = B["t"], B["side"]
    bF = np.where(G["ok"], B["first"] / G["fmid"] - 1, np.nan)
    sidx = {1: np.flatnonzero(bside > 0), -1: np.flatnonzero(bside < 0)}
    stime = {s: bt[i] for s, i in sidx.items()}
    jr = np.searchsorted(fe, fe - LL_REF_MS, "right") - 1
    r = np.where(jr >= 0, fm / fm[np.maximum(jr, 0)] - 1, 0.0)
    out = {}
    for x in xs:
        cand = np.flatnonzero((np.abs(r) >= x) & (fe >= t0) & (fe < t1 - HORIZON_MS) & (jr >= 0))
        fc = fe[cand]
        rows, pos = [], 0
        while pos < len(cand):
            c = int(cand[pos])
            tc = int(fe[c])
            d = 1.0 if r[c] > 0 else -1.0
            L0 = fm[jr[c]]
            kp = int(np.searchsorted(fe, tc + LL_PERSIST_MS, "right")) - 1
            L1 = fm[kp]
            ix0 = int(np.searchsorted(xe, tc - LL_REF_MS, "right")) - 1
            if d * (L1 / L0 - 1) < x or ix0 < 0 or np.isnan(bsx[ix0]) or dead_overlap(tc - 2 * LL_REF_MS, tc + HORIZON_MS, dead):
                pos += 1
                continue
            m = abs(L1 / L0 - 1)
            thr = m / 2
            bs0 = bsx[ix0]
            ixc = int(np.searchsorted(xe, tc, "right")) - 1
            led = d * (bsx[ixc] - bs0) >= thr
            j0 = int(np.searchsorted(bt, tc - LL_REF_MS, "right"))
            j1 = int(np.searchsorted(bt, tc, "right"))
            j2 = int(np.searchsorted(bt, tc + HORIZON_MS, "right"))
            b0 = np.full(j2 - j0, np.nan)          # опора каждой пачки — по её стороне
            have = False
            for s in (1, -1):
                p_ = int(np.searchsorted(stime[s], tc - LL_REF_MS, "right"))
                ii = sidx[s][max(0, p_ - 3):p_]
                if len(ii) and bt[ii[-1]] >= tc - HORIZON_MS:
                    v = bF[ii]
                    v = v[~np.isnan(v)]
                    if len(v):
                        b0[bside[j0:j2] == s] = np.median(v)
                        have = True
            dev = bF[j0:j2] - b0
            if not led and j1 > j0 and (d * dev[: j1 - j0] >= thr).any():
                led = True
            lag_t = lag_s = np.nan
            if not led:
                w = np.flatnonzero(np.abs(dev[j1 - j0:]) <= thr)
                if len(w):
                    lag_t = float(bt[j1 + int(w[0])] - tc)
                ke = int(np.searchsorted(xe, tc + HORIZON_MS, "right"))
                w = np.flatnonzero(np.abs(bsx[ixc + 1:ke] - bs0) <= thr)
                if len(w):
                    lag_s = float(xe[ixc + 1 + int(w[0])] - tc)
            rows.append((tc, d, float(led), lag_t, lag_s, float((X["ask"][ix0] - X["bid"][ix0]) / ((X["ask"][ix0] + X["bid"][ix0]) / 2)), m, float(j2 - j1), float(have)))
            pos = int(np.searchsorted(fc, tc + LL_REFRAC_MS, "left"))
        out[x] = np.array(rows, float).reshape(-1, 9)
    return out


# ---------- раздвижка по сделкам BingX против исполнимой цены F ----------

def centre_series(F, X, t1):
    """Объединённый ряд снимков пары (F, BingX) и авто-центр: (T, S, Bu, m0, cen) или None."""
    T, S, Bu, ia, ib = core.pair_series(F, X, t1)
    if len(T) < 50:
        return None
    mins, _, sb, bb = core.minute_bars(T, S, Bu)
    m0, cen = core.rolling_centre(mins, sb, bb)
    return T, S, Bu, m0, cen


def centre_at(t, m0, cen):
    return cen[np.clip(t // 60000 - m0, 0, len(cen) - 1)]


def trade_gaps(F, B, m0, cen, X=None):
    """По пачке: F на момент пачки и Sell/Buy с ценой BingX = цена первой сделки; если задан X — ещё Sell/Buy по снимку BingX на тот же момент.
    -> dict: ok (F известен), iF, fmid, S, Bu (bps), c (центр, NaN — нет), Ssn, Bsn (по снимку; NaN без X), imp (улучшение цены сделки против касания снимка, bps: покупка — ask/p − 1, продажа — p/bid − 1)."""
    iF = np.searchsorted(F["e"], B["t"], "right") - 1
    ok = iF >= 0
    iF = np.maximum(iF, 0)
    p = B["first"]
    out = {"ok": ok, "iF": iF, "fmid": (F["bid"][iF] + F["ask"][iF]) / 2, "S": (F["bid"][iF] / p - 1) * 1e4, "Bu": (F["ask"][iF] / p - 1) * 1e4,
           "c": centre_at(B["t"], m0, cen)}
    if X is not None:
        iX = np.searchsorted(X["e"], B["t"], "right") - 1
        okx = iX >= 0
        iX = np.maximum(iX, 0)
        out["Ssn"] = np.where(okx, (F["bid"][iF] / X["ask"][iX] - 1) * 1e4, np.nan)
        out["Bsn"] = np.where(okx, (F["ask"][iF] / X["bid"][iX] - 1) * 1e4, np.nan)
        out["imp"] = np.where(okx, np.where(B["side"] > 0, X["ask"][iX] / p - 1, p / X["bid"][iX] - 1) * 1e4, np.nan)
    return out


def trade_divergence(F, B, G, t0, t1, mode, thetas=THETAS, dead=None):
    """Раздвижка пачек BingX. mode 'c' — к центру, '0' — от нуля. -> dict: гистограммы, счётчики по суткам, эпизоды.
    Пачка «подходит», если агрессор-покупатель и Sell − c ≥ θ (вверх) либо агрессор-продавец и c − Buy ≥ θ (вниз).
    Эпизод начинает подходящая пачка, если прошлая подходящая того же направления была > 5 с назад. Длительность — до первого из:
    D_F — быстрая книга сошлась (bid_F < p·(1+(θ+c)) вверх, ask_F > p·(1+(c−θ)) вниз при неизменной цене BingX), D_B — следующая пачка BingX (любая сторона),
    по цене которой раздвижка < θ (доказательство, что цена BingX сдвинулась). inf — не сошлось за 10 мин / до конца окна."""
    edges = day_edges(t0, t1)
    nd = len(edges) - 1
    bt, side, first = B["t"], B["side"], B["first"]
    sh = G["c"] if mode == "c" else np.zeros(len(bt))
    valid = G["ok"] & ~np.isnan(sh)
    if dead is not None:
        valid &= ~dead_hit(bt, dead)
    up_g = G["S"] - sh          # раздвижка вверх (для пачки любой стороны: нижняя граница истинной)
    dn_g = sh - G["Bu"]
    di = day_idx(bt, edges)
    res = {"n_valid": np.zeros((2, nd), np.int64), "q": {}, "eps": {}, "hist": np.zeros((2, GAP_N), np.int64)}
    for s_i, (s, g) in enumerate(((1, up_g), (-1, dn_g))):
        m_s = valid & (side == s)
        res["n_valid"][s_i] = np.bincount(di[m_s], minlength=nd)
        res["hist"][s_i] = np.bincount(np.clip(np.floor(g[m_s] - GAP_LO).astype(np.int64), 0, GAP_N - 1), minlength=GAP_N)
    for th in thetas:
        for s_i, (s, g) in enumerate(((1, up_g), (-1, dn_g))):
            qual = valid & (side == s) & (g >= th)
            qi = np.flatnonzero(qual)
            res["q"][(th, s)] = (np.bincount(di[qi], minlength=nd), np.bincount(di[qi], weights=B["usd"][qi], minlength=nd))
            if not len(qi):
                res["eps"][(th, s)] = np.zeros((0, 8))
                continue
            starts = qi[np.r_[True, np.diff(bt[qi]) > EP_GAP_MS]]
            conv = np.flatnonzero(valid & (g < th))
            rows = []
            for i in starts:
                ti = int(bt[i])
                pos = int(np.searchsorted(conv, i, "right"))
                d_b = float(bt[conv[pos]] - ti) if pos < len(conv) and bt[conv[pos]] - ti <= HORIZON_MS else np.inf
                kF = int(G["iF"][i]) + 1
                end = int(np.searchsorted(F["e"], ti + HORIZON_MS, "right"))
                if s > 0:
                    k = first_where(F["bid"], kF, np.less, first[i] * (1 + (th + sh[i]) * 1e-4), end)
                else:
                    k = first_where(F["ask"], kF, np.greater, first[i] * (1 + (sh[i] - th) * 1e-4), end)
                d_f = float(F["e"][k] - ti) if k >= 0 else np.inf
                gs = (G["Ssn"][i] - sh[i]) if s > 0 else (sh[i] - G["Bsn"][i])
                rows.append((ti, g[i], d_f, d_b, B["usd"][i], B["n"][i], gs, G["imp"][i]))
            res["eps"][(th, s)] = np.array(rows, float).reshape(-1, 8)
    return res


def snapshot_divergence(T, S, Bu, m0, cen, B, G, t0, t1, mode, thetas=THETAS, dead=None):
    """Раздвижка по снимкам BingX (как вкладка «Арбитраж»): эпизоды = подряд идущие записи ряда пары с Sell − c ≥ θ (вверх) / c − Buy ≥ θ (вниз).
    Каждый эпизод проверяется сделками BingX внутри него: подтверждён — первая решающая пачка = агрессор-покупатель (вверх) / продавец (вниз), по цене которой
    раздвижка ≥ θ; опровергнут — первая решающая пачка показывает раздвижку < θ (цена BingX уже сошлась, снимок устарел); без проверки — решающей пачки нет.
    Решающая — любая пачка, кроме противоположной стороны с раздвижкой ≥ θ (та ничего не говорит о том, что исполнимо на нужной стороне).
    -> {(θ, s): {"runs": массив (n, 4) [начало, длит. мс, класс 0/1/2 = подтв./опровергнут/без проверки, день], "time": доля окна}}."""
    edges = day_edges(t0, t1)
    inw = T >= t0
    Tw, Sw, Bw = T[inw], S[inw], Bu[inw]
    sh = centre_at(Tw, m0, cen) if mode == "c" else np.zeros(len(Tw))
    bt, side = B["t"], B["side"]
    shb = G["c"] if mode == "c" else np.zeros(len(bt))
    validb = G["ok"] & ~np.isnan(shb)
    out = {}
    for s_i, (s, g_s, g_b) in enumerate(((1, Sw - sh, G["S"] - shb), (-1, sh - Bw, shb - G["Bu"]))):
        for th in thetas:
            runs = core.runs(Tw, g_s >= th, t1)
            if not runs:
                out[(th, s)] = {"runs": np.zeros((0, 4)), "time": 0.0}
                continue
            st = np.array([r[0] for r in runs], np.int64)
            du = np.array([r[1] for r in runs], np.int64)
            if dead is not None:   # эпизоды, пересекающие пропуск сбора, не считаются (книга за пропуском устарела)
                ds, de = dead
                k = np.searchsorted(de, st, "left")
                hit = (k < len(ds)) & (ds[np.minimum(k, len(ds) - 1)] <= st + du)
                st, du = st[~hit], du[~hit]
                if not len(st):
                    out[(th, s)] = {"runs": np.zeros((0, 4)), "time": 0.0}
                    continue
            qual_b = validb & (side == s) & (g_b >= th)
            dec = validb & ~((side == -s) & (g_b >= th))
            dpos = np.flatnonzero(dec)
            i0, i1 = np.searchsorted(bt, st, "left"), np.searchsorted(bt, st + du, "left")
            p = np.searchsorted(dpos, i0)
            pj = dpos[np.minimum(p, max(len(dpos) - 1, 0))] if len(dpos) else np.zeros(len(st), np.int64)
            has = (p < len(dpos)) & (pj < i1) if len(dpos) else np.zeros(len(st), bool)
            cls = np.where(has, np.where(qual_b[pj], 0, 1), 2) if len(dpos) else np.full(len(st), 2)
            out[(th, s)] = {"runs": np.column_stack([st, du, cls, day_idx(st, edges)]).astype(np.int64), "time": float(du.sum() / (t1 - t0))}
    return out
