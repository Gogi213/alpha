#!/usr/bin/env python3
"""Стадия 2 (Deck): по компонентам (пары с общими ногами) - эпизоды §5.2, сделки базовых правил B1/B3 и вариантов V1/V2, признаки ДО входа, серии подсказок.

Две половины считаются раздельно (как у протокола): F - формирование (сутки 2466..2478, ряд обрезан 2478, старт 2466), W - окно (2479..2499, старт «с нуля» с 2479, история с 2465).
Таблицы: <out>/ts_tables.npz (tr - сделки с признаками, ep - эпизоды, hints - серии |dev|>=theta, act - активные пары-сутки, cnt - счётчики, pairs - справочник пар).
Универсумы сделок: B1 = (θ 25,2; k 3; p 1; T 1440; S0 = 2·g_in; без потолка и Q6) - все сигналы; B3 = то же с p = 3 (подтверждение 3 минуты);
V1 / V2 = объявленные варианты П-38 r3 (воспроизведение окна 588 / 613 и формирования 356 / 359 для сверки).
"""
import argparse
import json
import math
import multiprocessing as mp
import os
import sys
import time
from collections import Counter

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ts_lib as T      # noqa: E402

L, CAL, W, H = T.L, T.CAL, T.W, T.H
UNIS = ["B1", "B3", "V1", "V2"]
UNI_I = {u: i for i, u in enumerate(UNIS)}
THETAS = H.THETAS
FEAT_NAMES = ["hour", "M1", "M3", "M7", "V1d", "V3d", "V7d", "F1", "F3", "F7", "drift7", "sig_t", "gsig", "staleA", "staleB", "rA", "rB", "share_drv", "drv", "turnA", "turnB",
              "fund_day", "rateA_day", "rateB_day", "age", "D"]
TR_COLS = list(L.TRADE_FIELDS) + ["uni", "half", "pi", "ai", "day", "byb", "exA", "exB"] + FEAT_NAMES
EP_COLS = ["half", "pi", "ai", "day", "t", "g_in", "gsig", "out", "dur", "adv", "dir", "D", "cens", "age", "byb", "exA", "exB"]
HB_EDGES = [1, 2, 5, 10, 30, 60, 1440]            # корзины длины: ==1, ==2, 3-5, 6-10, 11-30, 31-60, 61-1439, >=1440
NB = len(HB_EDGES) + 1
_C = {}


def init(nice):
    os.nice(nice)
    _C["world"] = T.make_world()
    _C["cpd"] = T.CR.class_params(T.CP_PATH)
    _C["q5"] = json.load(open(T.Q5_PATH, encoding="utf-8"))
    pairs = T.crypto_pairs(_C["world"])
    _C["pi"] = {p["pid"]: i for i, p in enumerate(sorted(pairs, key=lambda p: p["pid"]))}
    _C["ai"] = {k: i for i, k in enumerate(sorted({p["key"] for p in pairs}))}
    _C["cells"] = {}
    _C["celli"] = {c: i for i, c in enumerate(sorted({p["cell"] for p in pairs}))}


def load_cellc(cell, Lw):
    if cell not in _C["cells"]:
        _C["cells"][cell] = W.load_cell_c(T.OUT, cell, Lw)[0]
    return _C["cells"][cell]


# ------------------------------------------------------------------ признаки пары на полном ряду
def pair_tables(ps, frA, frB, nd):
    """Таблицы по суткам: медиана |d_idio|, разброс (1,4826·MAD), доля минут g >= 25,2 за предыдущие w = 1, 3, 7 ПОЛНЫХ суток; дрейф базиса |медиана s сутки-1 - медиана s 7 суток|.
    Окно считается только по суткам >= 1 (сутки 0 = разогрев, d там не определён с начала); нет данных - nan. Причинно: сутки k используют только сутки < k."""
    dI = ps.dI
    s = ps.s
    M = np.full((3, nd), np.nan)
    V = np.full((3, nd), np.nan)
    F = np.full((3, nd), np.nan)
    drift = np.full(nd, np.nan)
    for wi, w in enumerate((1, 3, 7)):
        for k in range(w + 1, nd):               # окно суток k-w..k-1, все >= 1
            seg = dI[(k - w) * MIN_DAY:k * MIN_DAY]
            seg = seg[np.isfinite(seg)]
            if len(seg) < 240:
                continue
            a = np.abs(seg)
            M[wi, k] = np.median(a)
            med = np.median(seg)
            V[wi, k] = 1.4826 * np.median(np.abs(seg - med))
            F[wi, k] = float(np.mean(a >= L.THETA_COST))
    for k in range(8, nd):
        s1 = s[(k - 1) * MIN_DAY:k * MIN_DAY]
        s7 = s[(k - 7) * MIN_DAY:k * MIN_DAY]
        s1, s7 = s1[np.isfinite(s1)], s7[np.isfinite(s7)]
        if len(s1) >= 240 and len(s7) >= 240:
            drift[k] = abs(float(np.median(s1)) - float(np.median(s7)))
    tabs = dict(M=M, V=V, F=F, drift=drift)
    for nm, fr in (("A", frA), ("B", frB)):
        rate, intv = last_rate(fr)
        tabs["rate" + nm] = rate
        tabs["int" + nm] = intv
    return tabs


def last_rate(fr):
    """Последняя известная выплата фандинга на каждой минуте (причинно) и интервал между двумя последними выплатами (минуты)."""
    N = len(fr)
    rate = np.full(N, np.nan)
    intv = np.full(N, np.nan)
    idx = np.flatnonzero(fr != 0)
    if len(idx) == 0:
        return rate, intv
    pos = np.searchsorted(idx, np.arange(N), side="right") - 1
    ok = pos >= 0
    rate[ok] = fr[idx[pos[ok]]]
    if len(idx) >= 2:
        ivf = np.concatenate([[np.nan], np.diff(idx).astype(float)])
        intv[ok] = ivf[pos[ok]]
    return rate, intv


MIN_DAY = T.MIN_DAY


def run_arrays(ps, ft, theta, k):
    """Серии E = (q4 и g >= max(theta, k·sigma, 25,2)) одного знака. -> (начало серии, конец серии) для каждой минуты (индексы)."""
    gent = L.gentry_array(ps.sig, theta, k)
    with np.errstate(invalid="ignore"):
        E = ft.q4 & (ps.g >= gent)
    code = np.where(E, ps.sgn, 0).astype(np.int8)
    n = len(code)
    chg = np.flatnonzero(code[1:] != code[:-1]) + 1
    bounds = np.concatenate([[0], chg, [n]])
    ln = np.diff(bounds)
    return np.repeat(bounds[:-1], ln), np.repeat(bounds[1:] - 1, ln)


def feature_matrix(ps, tabs, ts, dirs, run_start, run_end):
    """Признаки в минутах решения ts (только прошлое) -> матрица [len(ts), len(FEAT_NAMES)]."""
    ts = np.asarray(ts, np.int64)
    n = len(ts)
    X = np.full((n, len(FEAT_NAMES)), np.nan)
    if n == 0:
        return X
    k = ts // MIN_DAY
    X[:, 0] = (ts % MIN_DAY) // 60
    for wi in range(3):
        X[:, 1 + wi] = tabs["M"][wi, k]
        X[:, 4 + wi] = tabs["V"][wi, k]
        X[:, 7 + wi] = tabs["F"][wi, k]
    X[:, 10] = tabs["drift"][k]
    sig = ps.sig[ts]
    X[:, 11] = sig
    X[:, 12] = ps.g[ts] / sig
    X[:, 13] = ps.staleA[ts]
    X[:, 14] = ps.staleB[ts]
    tm = np.maximum(ts - 1, 0)
    with np.errstate(invalid="ignore", divide="ignore"):
        rA = 1e4 * (ps.cA[ts] / ps.cA[tm] - 1.0)
        rB = 1e4 * (ps.cB[ts] / ps.cB[tm] - 1.0)
        X[:, 15], X[:, 16] = rA, rB
        den = np.abs(rA) + np.abs(rB)
        X[:, 17] = np.where(den > 0, np.maximum(np.abs(rA), np.abs(rB)) / den, np.nan)
        X[:, 18] = np.where(np.isfinite(den), (np.abs(rB) > np.abs(rA)).astype(float), np.nan)
        X[:, 19] = ps.turnA[ts] if ps.turnA is not None else np.nan
        X[:, 20] = ps.turnB[ts] if ps.turnB is not None else np.nan
        dA = tabs["rateA"][ts] * MIN_DAY / tabs["intA"][ts] * 1e4
        dB = tabs["rateB"][ts] * MIN_DAY / tabs["intB"][ts] * 1e4
        X[:, 21] = np.asarray(dirs, float) * (dA - dB)
        X[:, 22], X[:, 23] = dA, dB
    X[:, 24] = ts - run_start[ts]
    X[:, 25] = run_end[ts] - ts + 1
    return X


# ------------------------------------------------------------------ задача компоненты
def _load(p, d1):
    cfg = L.Cfg(**T.CFG_OFFICIAL)
    return W.load_series(_C["world"], p, T.D0, d1, cfg, T.OUT, load_cellc(p["cell"], cfg.L), want_turn=True, want_fund=True)[0]


def _episodes(pss, cp, cfg_nc, p0):
    cpm = L.ClassParams(name=cp.name, m_r=cp.m_r, kstale=cp.kstale, q_c=cp.q_c)
    feats = [L.Feats(ps, cpm, cfg_nc, p0) for ps in pss]
    combo = L.Combo(L.THETA_COST, 3, 1, CAL.TMAX, None)
    sims = [CAL.EpisodeSim(ps, cpm, cfg_nc, combo, ft, p0, True) for ps, ft in zip(pss, feats)]
    import heapq
    heap = []
    for i, sm in enumerate(sims):
        t = sm.next_signal()
        if t is not None:
            heapq.heappush(heap, (t, sm.key, i))
    kept = set()
    while heap:
        t, _, i = heapq.heappop(heap)
        sm = sims[i]
        if cfg_nc.dedup and any((leg, t) in kept for leg in sm.ps.legs):
            sm.drop_dedup(t)
        else:
            for leg in sm.ps.legs:
                kept.add((leg, t))
            sm.execute(t)
        nt = sm.next_signal()
        if nt is not None:
            heapq.heappush(heap, (nt, sm.key, i))
    return sims, feats


def hints_acc(acc, ps, ai, celli, half, w_t_start, n_end):
    """Серии |dev| >= theta (отчёт подсказок, dev = s - b) - корзины длины по (актив, сутки старта, theta, половина, Bybit)."""
    dev = ps.s - ps.b
    if w_t_start:
        dev = dev.copy()
        dev[:w_t_start] = np.nan
    for ti, th in enumerate(THETAS):
        st, ln = H.run_lengths(dev[:n_end], th)
        if len(st) == 0:
            continue
        cens = (st + ln) >= n_end
        b = np.searchsorted(np.asarray(HB_EDGES), ln, side="left")          # ln==1 -> 0, ln==2 -> 1, 3-5 -> 2, ...; ln>=1441 -> 7
        b = np.where(ln >= HB_EDGES[-1], NB - 1, b)
        day = T.D0 + st // MIN_DAY
        for d in np.unique(day):
            m = day == d
            row = acc.setdefault((ai, int(d), ti, half, celli), np.zeros(2 * NB, np.int64))
            row[:NB] += np.bincount(b[m & ~cens], minlength=NB)
            row[NB:] += np.bincount(b[m & cens], minlength=NB)


def task(pairs):
    cx = _C
    cpd, q5 = cx["cpd"], cx["q5"]
    cfg_o = L.Cfg(**T.CFG_OFFICIAL)
    cfg_nc = L.Cfg(**{**T.CFG_OFFICIAL, "ceiling": False, "use_q6": False})
    cp = cpd["crypto"]
    cp2 = cpd["crypto"]
    out_tr, out_ep = [], []
    cnt = Counter()
    act = Counter()
    hacc = {}
    pssW = [_load(p, T.D_W1) for p in pairs]
    pssF = [_load(p, T.D_F1) for p in pairs]
    nd = T.D_W1 - T.D0 + 1
    tabs = []
    for p, ps in zip(pairs, pssW):
        frA = cx["world"].fund_array(p, 0, T.D0, T.D_W1)
        frB = cx["world"].fund_array(p, 1, T.D0, T.D_W1)
        tabs.append(pair_tables(ps, frA, frB, nd))
    byb = [int("bybit" in (p["exA"], p["exB"])) for p in pairs]
    pidx = [cx["pi"][p["pid"]] for p in pairs]
    aidx = [cx["ai"][p["key"]] for p in pairs]
    exa = [T.EXI[p["exA"]] for p in pairs]
    exb = [T.EXI[p["exB"]] for p in pairs]
    for half, pss in ((0, pssF), (1, pssW)):            # F: сигнальные сутки 2466..2478; W: с 2479 «с нуля»
        p0day = 2466 if half == 0 else T.D_W0
        period0 = (p0day - T.D0) * MIN_DAY
        n_end = (T.D_F1 - T.D0 + 1) * MIN_DAY if half == 0 else nd * MIN_DAY
        # --- эпизоды §5.2
        sims, feats = _episodes(pss, cp, cfg_nc, period0)
        for i, (sm, ft) in enumerate(zip(sims, feats)):
            if not sm.eps:
                continue
            rs, re_ = run_arrays(sm.ps, ft, L.THETA_COST, 3)
            ts = np.array([e[0] for e in sm.eps], np.int64)
            D = re_[ts] - ts + 1
            age = ts - rs[ts]
            cens = (re_[ts] >= sm.ps.N - 1).astype(float)
            for j, e in enumerate(sm.eps):
                out_ep.append((half, pidx[i], aidx[i], e[6], e[0], e[1], e[2], e[3], e[4], e[5], e[8], D[j], cens[j], age[j], byb[i], exa[i], exb[i]))
        # --- сделки по универсумам
        for uni in UNIS:
            if uni in T.BASE_COMBOS:
                combo, cfg, adm = T.BASE_COMBOS[uni], cfg_nc, None
                cpu = cp
            else:
                combo, cfg = T.V_COMBOS[uni], cfg_o
                cpu = cp2
                adm = None
                if uni == "V1":
                    adm = [bool(q5.get(p["pid"]) is not None and q5[p["pid"]] <= combo.T / 4.0) for p in pairs]
            fts = [L.Feats(ps, cpu, cfg, period0) for ps in pss]
            sims = L.simulate_component(pss, cpu, cfg, combo, fts, period0, adm)
            for i, sm in enumerate(sims):
                cnt.update({(uni, half, k): v for k, v in sm.cnt.items()})
                for d in sm.active_days():
                    act[(UNI_I[uni], half, int(d))] += 1
                if not sm.trades:
                    continue
                tr = np.array(sm.trades, np.float64)
                ts = tr[:, 0].astype(np.int64)
                dirs = tr[:, 4]
                rs, re_ = run_arrays(sm.ps, fts[i], combo.theta, combo.k)
                X = feature_matrix(sm.ps, tabs[i], ts, dirs, rs, re_)
                day = T.D0 + ts // MIN_DAY
                meta = np.column_stack([np.full(len(ts), UNI_I[uni]), np.full(len(ts), half), np.full(len(ts), pidx[i]), np.full(len(ts), aidx[i]), day,
                                        np.full(len(ts), byb[i]), np.full(len(ts), exa[i]), np.full(len(ts), exb[i])])
                out_tr.append(np.column_stack([tr, meta, X]))
        # --- серии подсказок (dev = s - b)
        for i, ps in enumerate(pss):
            hints_acc(hacc, ps, aidx[i], cx["celli"][pairs[i]["cell"]], half, 0 if half == 0 else period0, n_end)
    ep = np.array(out_ep, np.float64) if out_ep else np.zeros((0, len(EP_COLS)))
    tr = np.vstack(out_tr) if out_tr else np.zeros((0, len(TR_COLS)))
    return dict(tr=tr, ep=ep, cnt=cnt, act=act, hints=hacc, n=len(pairs))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threads", type=int, default=3)
    ap.add_argument("--limit", type=int, default=0, help="только первые N активов (проба)")
    ap.add_argument("--out", default=os.path.join(T.OUT, "out", "ts_tables.npz"))
    a = ap.parse_args()
    world = T.make_world()
    pairs = T.crypto_pairs(world)
    if a.limit:
        keys = sorted({p["key"] for p in pairs})[:a.limit]
        pairs = [p for p in pairs if p["key"] in keys]
    T.log_read("extract: пар %d, сутки %d..%d (читатель без защиты, post hoc)" % (len(pairs), T.D0, T.D_W1))
    comps = W.components(pairs)
    comps.sort(key=lambda c: -len(c))
    print("пар %d, компонент %d" % (len(pairs), len(comps)), flush=True)
    allp = sorted(T.crypto_pairs(world), key=lambda p: p["pid"])
    pairs_tab = [dict(pid=p["pid"], key=p["key"], cell=p["cell"], exA=p["exA"], exB=p["exB"], symA=p["symA"], symB=p["symB"]) for p in allp]
    t0 = time.time()
    trs, eps = [], []
    cnt, act, hints = Counter(), Counter(), {}
    ctx = mp.get_context("fork")
    done = 0
    with ctx.Pool(a.threads, initializer=init, initargs=(10,)) as pool:
        for r in pool.imap_unordered(task, comps, chunksize=1):
            trs.append(r["tr"])
            eps.append(r["ep"])
            cnt.update(r["cnt"])
            act.update(r["act"])
            for k, v in r["hints"].items():
                if k in hints:
                    hints[k] += v
                else:
                    hints[k] = v.copy()
            done += r["n"]
            if len(trs) % 25 == 0:
                print("  компонент %d/%d, пар %d, %.0f с" % (len(trs), len(comps), done, time.time() - t0), flush=True)
    tr = np.vstack(trs)
    ep = np.vstack(eps)
    hk = sorted(hints)
    hints_arr = np.array([list(k) + hints[k].tolist() for k in hk], np.int64)
    act_arr = np.array([[k[0], k[1], k[2], v] for k, v in sorted(act.items())], np.int64)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    np.savez_compressed(a.out, tr=tr, ep=ep, hints=hints_arr, act=act_arr)
    json.dump(dict(tr_cols=TR_COLS, ep_cols=EP_COLS, unis=UNIS, pairs=pairs_tab, assets=sorted({p["key"] for p in allp}), cells=sorted({p["cell"] for p in allp}), exch=T.EXCH, hints_cols=["ai", "day", "theta_i", "half", "celli"] +
                   ["u_b%d" % i for i in range(NB)] + ["c_b%d" % i for i in range(NB)], hb_edges=HB_EDGES, thetas=list(THETAS),
                   cnt={"%s|%d|%s" % k: v for k, v in sorted(cnt.items())}, seconds=round(time.time() - t0, 1)),
              open(a.out.replace(".npz", ".json"), "w", encoding="utf-8"), ensure_ascii=False)
    print("готово: сделок %d, эпизодов %d, строк серий %d, %.0f с" % (len(tr), len(ep), len(hints_arr), time.time() - t0))


if __name__ == "__main__":
    main()
