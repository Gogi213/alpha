#!/usr/bin/env python3
"""П-42 §13.26 (TK-604): пробиваемость монеты — эпизоды U0 → признак `bk` на кандидате (причинно), таблицы `--ftab`, множества исключённых, дубли клеток,
покрытие G5, ступень 1 (описательно). Только numpy и stdlib; движок не запускает, aggTrades не читает (метки приходят из `tk604_episodes.py`).

Признак (п. 4): эпизод пригоден в момент `t`, если `sig_e + (T + L) ≤ t`, T = 60 мин, L = 5 мин (граница `=` включена); `n` — число закрытых эпизодов той же
стороны той же монеты, `k` — из них `y = 1`, `bk = k / n` (пусто при `n < K`). История — склейка эпизодов `w0 wa wb wc` по зачётным диапазонам, одна на кандидата
независимо от окна, из которого он взят.
"""
from __future__ import annotations

import csv
import dataclasses
import datetime

import numpy as np

import tk604_stats as st

MIN_MS = 60_000
DAY_MS = st.DAY_MS
T_MIN, L_MIN = 60, 5
CLOSE_MS = (T_MIN + L_MIN) * MIN_MS            # эпизод закрыт к t, если sig + CLOSE_MS ≤ t
TRADE_L_MS = L_MIN * MIN_MS                    # сделка пригодна к t, если exit + L ≤ t (только ступень 1)
KS = st.GRID_K
BIG = 1 << 42                                  # ключ (монета, время): монета·BIG + мс эпохи (< 2^42)
D0_MS = int(datetime.datetime(2026, 7, 1, tzinfo=datetime.timezone.utc).timestamp() * 1000)
SIDE_NAME = {"L": "long", "S": "short"}
EP_COLS = ["side", "sym", "level", "sig_ms", "indep", "ok_t60", "y", "y_n", "natr_e8", "lp_e8", "in_pool", "locate_fail"]


# ---- эпизоды -----------------------------------------------------------------------------------------------------------------

@dataclasses.dataclass
class Episodes:
    """Эпизоды U0 (независимые подходы), по одной записи на эпизод: сторона, монета, абсолютное время первого сигнала (мс эпохи), метки."""
    side: np.ndarray       # 'long' / 'short'
    sym: np.ndarray        # имя монеты
    abs_ms: np.ndarray     # int64
    y: np.ndarray          # int8: метка o_x1_t60_f01 == O3
    y_n: np.ndarray        # int8: метка при X = 1 NATR (−1 нет)
    natr_e8: np.ndarray    # int64
    in_pool: np.ndarray    # bool: сигнал есть в b0 (пул включён)

    def __len__(self):
        return len(self.abs_ms)

    def take(self, m):
        return Episodes(self.side[m], self.sym[m], self.abs_ms[m], self.y[m], self.y_n[m], self.natr_e8[m], self.in_pool[m])


def read_episodes_csv(path):
    """CSV `tk604_episodes.py` одного окна → список словарей (значения строками)."""
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def window_rows_to_arrays(rows, win):
    """Строки окна → массивы; `abs_ms = T0 + sig_ms`. Пригодны только строки с меткой (ok_t60 = 1 и y не пусто) и independent (indep = 1)."""
    keep = [r for r in rows if r["indep"] == "1" and r["ok_t60"] == "1" and r["y"] != ""]
    n = len(keep)
    abs_ms = np.array([int(r["sig_ms"]) for r in keep], dtype=np.int64) + win.t0_ms if n else np.zeros(0, dtype=np.int64)
    return Episodes(np.array([r["side"] for r in keep], dtype=object), np.array([r["sym"] for r in keep], dtype=object), abs_ms,
                    np.array([int(r["y"]) for r in keep], dtype=np.int8), np.array([int(r["y_n"]) if r["y_n"] != "" else -1 for r in keep], dtype=np.int8),
                    np.array([int(r["natr_e8"]) if r["natr_e8"] != "" else -1 for r in keep], dtype=np.int64),
                    np.array([r["in_pool"] == "1" for r in keep], dtype=bool))


def _day_ms(d):
    return int(datetime.datetime(d.year, d.month, d.day, tzinfo=datetime.timezone.utc).timestamp() * 1000)


def in_range(abs_ms, win):
    """Маска: UTC-дата абсолютного времени в зачётном диапазоне окна (включительно)."""
    a = np.asarray(abs_ms, dtype=np.int64)
    return (a >= _day_ms(win.lod)) & (a < _day_ms(win.hid) + DAY_MS)


def splice_episodes(per_window, windows=st.HIST_WINDOWS):
    """Склейка эпизодов окон по зачётным диапазонам (`w0` 17–30.06, `wa`, `wb`, `wc`). per_window: {имя: Episodes (после window_rows_to_arrays)}.
    Охрана: окна только разрешённые; ни одного эпизода позже 13.11; диапазоны не пересекаются (эпизод принадлежит ровно одному окну)."""
    st.check_isolation(windows)
    parts, prev_hi = [], None
    for w in windows:
        if prev_hi is not None and w.lod <= prev_hi:
            raise st.GuardError("эпизоды: диапазон %s (%s) наезжает на предыдущий (до %s)" % (w.name, w.lo, prev_hi))
        prev_hi = w.hid
        e = per_window[w.name]
        st.check_abs_ms(e.abs_ms, "время эпизода окна %s" % w.name)       # отказ, а не молчаливая отбраковка: данные после 13.11 принимать нельзя
        parts.append(e.take(in_range(e.abs_ms, w)))
    out = Episodes(*(np.concatenate([getattr(p, f.name) for p in parts]) for f in dataclasses.fields(Episodes)))
    st.check_abs_ms(out.abs_ms, "время эпизода")
    return out


# ---- история и признак -------------------------------------------------------------------------------------------------------

class History:
    """Накопление по (монета): события (время закрытия, значение). `counts(sym, t)` — число и сумма значений событий с закрытием ≤ t (вектор по запросам).
    Эпизоды: закрытие = sig + CLOSE_MS, значение y; прошлые сделки: закрытие = exit + L, значение net."""

    def __init__(self, sym, close_ms, val):
        sym = np.asarray(sym, dtype=object)
        self.names = sorted(set(sym.tolist()))
        self.ix = {s: i for i, s in enumerate(self.names)}
        sid = np.array([self.ix[s] for s in sym], dtype=np.int64)
        close = np.asarray(close_ms, dtype=np.int64)
        if len(close) and int(close.max()) >= BIG:
            raise st.GuardError("History: время %d ≥ 2^42" % int(close.max()))
        key = sid * BIG + close
        o = np.argsort(key, kind="stable")
        self.key = key[o]
        self.cum = np.concatenate([[0.0], np.cumsum(np.asarray(val, dtype=float)[o])])

    def counts(self, sym, t_ms, within_ms=None):
        """(n, сумма) событий монеты с закрытием в (t − within, t] (within=None — вся история до t). Монеты без событий — (0, 0)."""
        sym = np.asarray(sym, dtype=object)
        t = np.asarray(t_ms, dtype=np.int64)
        sid = np.array([self.ix.get(s, -1) for s in sym], dtype=np.int64)
        known = sid >= 0
        sid_k = np.where(known, sid, 0)
        hi = np.searchsorted(self.key, sid_k * BIG + t, side="right")
        lo_t = np.zeros_like(t) if within_ms is None else np.maximum(t - within_ms, 0)
        lo = np.searchsorted(self.key, sid_k * BIG + lo_t, side="right") if within_ms is not None else np.searchsorted(self.key, sid_k * BIG, side="left")
        n = np.where(known, hi - lo, 0)
        s = np.where(known, self.cum[hi] - self.cum[lo], 0.0)
        return n.astype(np.int64), s


def episode_history(eps, side):
    """История эпизодов одной стороны ('long'/'short')."""
    m = eps.side == side
    return History(eps.sym[m], eps.abs_ms[m] + CLOSE_MS, eps.y[m].astype(float))


def features(hist, sym, t_ms):
    """`n`, `k` и `bk20/40/60` на кандидатах (монета, абсолютное время сигнала). bk = k / n при n ≥ K, иначе NaN."""
    n, k = hist.counts(sym, t_ms)
    k = np.rint(k).astype(np.int64)
    with np.errstate(invalid="ignore", divide="ignore"):
        bk = {K: np.where(n >= K, k / np.maximum(n, 1), np.nan) for K in KS}
    return n, k, bk


def excluded(n, k, K, tau):
    """Правило п. 6: исключён, если `n ≥ K` и `bk < τ` (деление и сравнение в float64 — как у движка: `fv` из той же десятичной записи)."""
    with np.errstate(invalid="ignore", divide="ignore"):
        return (n >= K) & (k / np.maximum(n, 1) < tau)


# ---- таблица --ftab ----------------------------------------------------------------------------------------------------------

def fmt_val(x):
    x = float(x)
    return "" if x != x else repr(x)


def write_ftab(path, sym, sig_ms, n, k, bk):
    """CSV `sym,sig_ms,n,k,bk20,bk40,bk60` (пусто = нет значения); строки по ключу (sym, sig_ms) уникальны, порядок — по (sym, sig_ms)."""
    sym = np.asarray(sym, dtype=object)
    order = sorted(range(len(sym)), key=lambda i: (sym[i], int(sig_ms[i])))
    seen = set()
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("sym,sig_ms,n,k,bk20,bk40,bk60\n")
        for i in order:
            key = (sym[i], int(sig_ms[i]))
            if key in seen:
                continue
            seen.add(key)
            f.write("%s,%d,%d,%d,%s,%s,%s\n" % (sym[i], int(sig_ms[i]), int(n[i]), int(k[i]), fmt_val(bk[20][i]), fmt_val(bk[40][i]), fmt_val(bk[60][i])))
    return len(seen)


def read_ftab(path):
    """Читает таблицу обратно (для проверок и независимого пересчёта G8): sym, sig_ms, n, k."""
    sym, ms, n, k = [], [], [], []
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            sym.append(r["sym"]); ms.append(int(r["sig_ms"])); n.append(int(r["n"])); k.append(int(r["k"]))
    return np.array(sym, dtype=object), np.array(ms, dtype=np.int64), np.array(n, dtype=np.int64), np.array(k, dtype=np.int64)


def read_sigsx_keys(path):
    """Ключи кандидатов из `<side>-sigsx.csv` дампа `P42_SIGSX` (в заголовке бинаря 9 имён при 10 полях строки — читаем по позициям: 0 — sym, 2 — sig_ms, 8 — natr_e8)."""
    sym, ms = [], []
    with open(path, encoding="utf-8", newline="") as f:
        rd = csv.reader(f)
        next(rd)
        for r in rd:
            sym.append(r[0]); ms.append(int(r[2]))
    return np.array(sym, dtype=object), np.array(ms, dtype=np.int64)


# ---- клетки: множества исключённых, дубли, матрица пересечений ---------------------------------------------------------------

def exclusion_masks(n, k):
    """{cid: булев массив по кандидатам} для всех 9 клеток сетки."""
    return {st.cell_id(K, t): excluded(n, k, K, t) for K, t in st.GRID}


def find_duplicates(masks):
    """Дубли клеток (п. 6, критик): порядок K по возрастанию, внутри — τ по возрастанию; клетка с множеством, равным уже учтённой, — дубль её (`cid → dup_of` или None)."""
    seen, out = {}, {}
    for K, t in st.GRID:
        cid = st.cell_id(K, t)
        key = np.packbits(masks[cid]).tobytes() + len(masks[cid]).to_bytes(8, "little")
        if key in seen:
            out[cid] = seen[key]
        else:
            seen[key] = cid
            out[cid] = None
    return out


def jaccard_matrix(masks):
    """Матрица Jaccard 9 × 9 множеств исключённых (счётчики, без P&L): (идентификаторы клеток, матрица)."""
    ids = [st.cell_id(K, t) for K, t in st.GRID]
    m = np.zeros((len(ids), len(ids)))
    for i, a in enumerate(ids):
        for j, b in enumerate(ids):
            m[i, j] = st.jaccard(masks[a], masks[b])
    return ids, m


# ---- G5: покрытие ------------------------------------------------------------------------------------------------------------

def abs_to_day(abs_ms):
    return (np.asarray(abs_ms, dtype=np.int64) - D0_MS) // DAY_MS


def coverage(cand_abs, cand_n, trade_day, trade_n, trade_k, sigma=None):
    """G5 (без P&L: только счётчики и n, k): по месяцам и K — доля кандидатов и число сделок базы с n ≥ K; покрытые сутки (первые сутки, с которых у ≥ 5 % сделок значение);
    по клетке (K, τ): N_cov, E, f_cov = E / N_cov, f_all = E / N. K «читается», если хотя бы в одном месяце ≥ 5 % кандидатов имеют значение (п. 5)."""
    cd = abs_to_day(cand_abs)
    cm = np.full(len(cd), -1)
    ok = (cd >= 0) & (cd < st.NDAYS)
    cm[ok] = st.MONTH_OF_DAY[cd[ok]]
    tm = st.MONTH_OF_DAY[trade_day]
    out = {"N": int(len(trade_day)), "by_K": {}, "cells": {}, "readable": {}}
    for K in KS:
        share = [float(((cand_n >= K) & (cm == m)).sum() / max((cm == m).sum(), 1)) for m in range(len(st.MONTH_LEN))]
        trades = [int(((trade_n >= K) & (tm == m)).sum()) for m in range(len(st.MONTH_LEN))]
        per_day = np.array([((trade_n >= K) & (trade_day == d)).sum() / max((trade_day == d).sum(), 1) if (trade_day == d).any() else np.nan for d in range(st.NDAYS)])
        first = next((int(d) for d in range(st.NDAYS) if per_day[d] == per_day[d] and per_day[d] >= 0.05), None)
        out["by_K"][K] = dict(cand_share=share, trades=trades, first_cov_day=first, covered_days=None if first is None else st.NDAYS - first)
        out["readable"][K] = bool(any(s >= 0.05 for s in share))
    for K, t in st.GRID:
        cov = trade_n >= K
        n_cov = int(cov.sum())
        e = int(excluded(trade_n, trade_k, K, t).sum())
        out["cells"][st.cell_id(K, t)] = dict(N_cov=n_cov, E=e, f_cov=e / n_cov if n_cov else float("nan"), f_all=e / len(trade_day) if len(trade_day) else float("nan"))
    return out


# ---- ступень 1 (описательно) -------------------------------------------------------------------------------------------------

def auc(x, y):
    """AUC признака x против метки y ∈ {0,1}; ничья — ½; NaN в x исключаются. Ранги со средним по ничьим."""
    x, y = np.asarray(x, dtype=float), np.asarray(y)
    ok = ~np.isnan(x)
    x, y = x[ok], y[ok].astype(bool)
    n1, n0 = int(y.sum()), int((~y).sum())
    if n1 == 0 or n0 == 0:
        return float("nan")
    _, inv, cnt = np.unique(x, return_inverse=True, return_counts=True)
    cs = np.cumsum(cnt)
    rank = (cs - (cnt - 1) / 2.0)[inv]                 # средний ранг группы (1-based)
    return float((rank[y].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0))


def wilson(k, n, z=1.96):
    if n == 0:
        return float("nan"), float("nan")
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * (p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5 / d
    return c - h, c + h


def _day_blocks(days, nboot, block, rng):
    ud = np.unique(days)
    nd = len(ud)
    by = {d: np.flatnonzero(days == d) for d in ud}
    nb = -(-nd // block)
    for _ in range(nboot):
        starts = rng.integers(0, nd, size=nb)
        sel = ud[(starts[:, None] + np.arange(block)[None, :]).ravel() % nd][:nd]
        yield np.concatenate([by[d] for d in sel])


def _coin_clusters(coins, nboot, rng):
    uc = np.unique(coins)
    by = {c: np.flatnonzero(coins == c) for c in uc}
    for _ in range(nboot):
        yield np.concatenate([by[c] for c in rng.choice(uc, size=len(uc), replace=True)])


def auc_ci(x, y, days, coins, nboot=1000, block=4, seed=604):
    """AUC и интервалы двух бутстрепов — блоки по суткам (блок 4) и по монетам; p — большая из двух (как §13.17; p = 2·min(доля ≤ ½, доля ≥ ½), сглаживание +1). Описательно: 1000 повторов."""
    x, y, days, coins = np.asarray(x, dtype=float), np.asarray(y), np.asarray(days), np.asarray(coins)
    ok = ~np.isnan(x)
    x, y, days, coins = x[ok], y[ok], days[ok], coins[ok]
    a = auc(x, y)
    if a != a or len(x) < 30:
        return dict(auc=a, n=int(len(x)), lo_d=float("nan"), hi_d=float("nan"), lo_c=float("nan"), hi_c=float("nan"), p=float("nan"))
    rng = np.random.default_rng(seed)
    out = {}
    for name, gen in (("d", _day_blocks(days, nboot, block, rng)), ("c", _coin_clusters(coins, nboot, rng))):
        v = np.array([auc(x[i], y[i]) for i in gen])
        v = v[~np.isnan(v)]
        out[name] = (float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5)), float(min(1.0, (2 * min((v <= 0.5).sum(), (v >= 0.5).sum()) + 1) / (len(v) + 1))))
    return dict(auc=a, n=int(len(x)), lo_d=out["d"][0], hi_d=out["d"][1], lo_c=out["c"][0], hi_c=out["c"][1], p=max(out["d"][2], out["c"][2]))


def strata_edges(bk, n, n_min=10):
    """Границы терцилей bk по эпизодам с n ≥ 10 (на всей выборке стороны); если bk = 0 занимает ≥ 1/3 массы — отдельная страта, терцили — по остальным."""
    m = n >= n_min
    v = bk[m]
    if len(v) == 0:
        return dict(zero_alone=False, edges=[])
    zero_alone = float((v == 0).mean()) >= 1 / 3
    w = v[v > 0] if zero_alone else v
    e = [float(np.quantile(w, 1 / 3)), float(np.quantile(w, 2 / 3))] if len(w) else []
    return dict(zero_alone=zero_alone, edges=e)


def assign_strata(bk, n, spec, n_min=10):
    """Страты: 'мало истории' (n < 10), 'bk=0' (если отдельная), далее 'T1' … по границам."""
    out = np.empty(len(bk), dtype=object)
    for i in range(len(bk)):
        if n[i] < n_min:
            out[i] = "мало истории"
        elif spec["zero_alone"] and bk[i] == 0:
            out[i] = "bk=0"
        else:
            j = sum(1 for e in spec["edges"] if bk[i] > e)
            out[i] = "T%d" % (j + 1)
    return out


def trade_strata(bk20, spec_edges):
    """Страты сделок базы по bk20 (п. 5 (ii)): нет значения / bk20 = 0 / ниже границы терцилей / выше; границу задаёт ступень (i) (нижняя граница верхнего терциля = edges[0])."""
    out = np.empty(len(bk20), dtype=object)
    lo = spec_edges["edges"][0] if spec_edges["edges"] else float("nan")
    for i, v in enumerate(bk20):
        out[i] = "нет значения" if v != v else ("bk20=0" if v == 0 else ("ниже терцилей" if v <= lo else "выше терцилей"))
    return out


def mean_by_group(val, group, order=None):
    """По группам: число, сумма, среднее на сделку, доля в плюсе."""
    out = {}
    for g in (order or sorted(set(group.tolist()))):
        m = group == g
        v = val[m]
        out[g] = dict(n=int(m.sum()), total=float(v.sum()), mean=float(v.mean()) if len(v) else float("nan"), pos=float((v > 0).mean()) if len(v) else float("nan"))
    return out


# ---- G3 (реальные данные): причинность пересчётом по обрезанному журналу ------------------------------------------------------

def brute_nk(eps_sym, eps_abs, eps_y, sym, t):
    """Независимый пересчёт `n`, `k` перебором: эпизоды монеты с sig + CLOSE_MS ≤ t."""
    n = k = 0
    for s, a, y in zip(eps_sym, eps_abs, eps_y):
        if s == sym and a + CLOSE_MS <= t:
            n += 1
            k += int(y)
    return n, k


def garbage_after(eps, t, seed):
    """Журнал, обрезанный на t: эпизоды с закрытием > t заменены мусором (метка случайная, время сдвинуто случайно ПОЗЖЕ t) — признак на t обязан остаться тем же."""
    rng = np.random.default_rng(seed)
    late = eps.abs_ms + CLOSE_MS > t
    abs_ms = eps.abs_ms.copy()
    y = eps.y.copy()
    k = int(late.sum())
    abs_ms[late] = t + CLOSE_MS + rng.integers(1, 90 * DAY_MS, size=k)
    y[late] = rng.integers(0, 2, size=k).astype(np.int8)
    return dataclasses.replace(eps, abs_ms=abs_ms, y=y)


def pick_candidates(cand_side, cand_abs, seams_ms, total=50, per_seam_side=10, half_days=3, seed=604):
    """Выборка G3: `total` случайных кандидатов + ≥ `per_seam_side` на каждой стороне в ±`half_days` сут. вокруг каждого шва (мс эпохи). Возвращает индексы."""
    rng = np.random.default_rng(seed)
    pick = set(rng.choice(len(cand_abs), size=min(total, len(cand_abs)), replace=False).tolist())
    for seam in seams_ms:
        near = np.abs(np.asarray(cand_abs) - seam) <= half_days * DAY_MS
        for side in ("long", "short"):
            idx = np.flatnonzero(near & (np.asarray(cand_side) == side))
            if len(idx):
                pick.update(rng.choice(idx, size=min(per_seam_side, len(idx)), replace=False).tolist())
    return sorted(pick)


def causality_check(eps, cand_side, cand_sym, cand_abs, picks, seed=604):
    """G3: для каждого выбранного кандидата признак из полной склейки == признаку из журнала с мусором после t == независимому пересчёту перебором. Возвращает список расхождений."""
    full = {s: episode_history(eps, s) for s in ("long", "short")}
    bad = []
    for j, i in enumerate(picks):
        side, sym, t = cand_side[i], cand_sym[i], int(cand_abs[i])
        nf, kf = full[side].counts(np.array([sym], dtype=object), np.array([t]))
        g = garbage_after(eps, t, seed + j)
        ng, kg = episode_history(g, side).counts(np.array([sym], dtype=object), np.array([t]))
        m = eps.side == side
        nb, kb = brute_nk(eps.sym[m], eps.abs_ms[m], eps.y[m], sym, t)
        if not (int(nf[0]) == int(ng[0]) == nb and int(round(kf[0])) == int(round(kg[0])) == kb):
            bad.append((side, sym, t, (int(nf[0]), int(round(kf[0]))), (int(ng[0]), int(round(kg[0]))), (nb, kb)))
    return bad
