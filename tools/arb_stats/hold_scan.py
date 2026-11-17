"""Эпизоды расхождения, которые ДЕРЖАТСЯ непрерывно: пара (монета × две биржи), расхождение ≥ θ (0,5 % и 1 %) в двух видах.

  «от нуля» — исполнимый разрыв цен между биржами: sell-сторона по биду минус buy-сторона по оферу (Sell = bid_A/ask_B − 1 ≥ θ или −Buy = 1 − ask_A/bid_B ≥ θ), центр не нужен;
  «от центра» — Sell − c ≥ θ или c − Buy ≥ θ, c — причинный авто-центр (медиана минутных баров ряда за прошлые 30 мин, core.rolling_centre; сдвиг дольше 30 мин становится новым центром).
Эпизод — подряд идущие моменты ряда (изменение лучшей цены любой из двух ног), где условие верно; длительность — от первого момента до первого момента без него (метка E).
Хранятся ВСЕ эпизоды длительностью ≥ 1 с (слово владельца «все кейсы», без верхней границы); короче — только счётчики по порогам 1…500 мс.
Устаревание НЕ режется (в отличие от tokyo_run): у каждого эпизода пишется наибольший возраст лучшей цены каждой ноги за эпизод — фильтр «нога устарела > 5 с» делает hold_filter.py.
Исполнимость (simulate): на каждом эпизоде вход по сигналу первого момента с задержкой d = 1/3/5 с на обеих ногах по реальной книге того момента (вход происходит, даже если расхождение уже ушло),
выход тейкером через d после сигнала выхода «к центру» (c-t), «к нулю» (z-t) или «из − в +» (x1-t: противоположный разрыв ≥ θ от базы вида — центра или нуля), не дольше 1 ч удержания (потом закрытие по рынку); одна позиция на пару и настройку;
комиссия круга 4 × 3,15 = 12,6 bps. Результат по эпизоду: net, gross (bps), размер вершины на входе ($), моменты исполнения входа и выхода (мс), цена входа (bps), центр на входе.
Допускающий вариант (HOLD_TOL_MS > 0): провал ниже порога короче HOLD_TOL_MS, между двумя участками «условие верно», эпизод не рвёт (мерцание тонких книг); столбцы ndip / tdip — число и суммарная длина провалов внутри.
HOLD_SIM=0 — без исполнимости (только эпизоды): она считается потом на оставшихся (hold_resim.py).
Запуск: python3 hold_scan.py <каталог книг (с parsed/<биржа>/)> <t0_мс> <t1_мс> <выход> [PAR]; пишет <выход>.partNNN по 50 монет (склейка — hold_table.py); переменные: COINS (проба), MAXTASKS, HOLD_TOL_MS, HOLD_SIM.
"""
import os
import pickle
import sys
import time
import warnings
from multiprocessing import Pool
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import core  # noqa: E402
import maker_run  # noqa: E402
from hold_common import CAP_MS, CENTRE_MIN, EX5, FEE_RT_BPS, THETAS, build_coins, day_edges, unit_of  # noqa: E402

COLS = ("t", "dur", "side", "open", "i0", "ageA", "ageB", "nA", "nB", "size0", "sizemin", "edge0", "edgemax", "c0", "trend", "price", "priceB", "spA", "spB", "npts", "ndip", "tdip")
CI = {c: i for i, c in enumerate(COLS)}
MIN_KEEP_MS = 1000
SHORT_MS = (1, 10, 50, 100, 250, 500)
DELAYS = (1000, 3000, 5000)
KINDS = ("c-t", "z-t", "x1-t")
SIM_COLS = ("net", "gross", "size", "tx", "te", "edge_tx", "c_tx", "how", "pl", "plsize")   # how: 1 — закрыт по правилу выхода, 2 — по потолку удержания; pl, plsize — плацебо (среднее net и размер по случайным входам)
HOLD_CAP_MS = 1_800_000                    # потолок удержания позиции: 30 мин (дольше — закрытие по рынку; владелец 17.11: что держится > 30 мин — неинтересно)
DEV_MIN_BPS = 50.0                         # «от нуля», выход «к центру»: вход только при отклонении от центра ≥ 50 bps в момент сигнала (иначе сигнал выхода уже стоит на входе)
PLACEBO_K = 5                              # случайных входов на каждый настоящий круг
TOL_MS = int(os.environ.get("HOLD_TOL_MS", "0"))      # 0 — строгие эпизоды; > 0 — допускающий вариант (провалы короче TOL_MS не рвут эпизод)
DO_SIM = os.environ.get("HOLD_SIM", "1") != "0"
MISMATCH_BPS = 1000.0                      # медиана разрыва пары ≥ 10 % — другой контракт (множитель, одноимённая другая монета): эпизодов не считаем
G = {}


def runs_of(mask):
    """Начала и концы (включительно) подряд идущих истинных участков."""
    d = np.diff(np.r_[False, mask, False].astype(np.int8))
    return np.flatnonzero(d == 1), np.flatnonzero(d == -1) - 1


def bridge_dips(cond, T, Tn, tol):
    """Заполняет провалы (участки cond = False) короче tol мс, лежащие между двумя участками cond = True. -> (cond с заполненными провалами, вес-время заполненного по моментам ряда, начала провалов 0/1)."""
    n = len(cond)
    wdip, dstart = np.zeros(n), np.zeros(n)
    if tol <= 0 or n < 3:
        return cond, wdip, dstart
    st, en = runs_of(~cond)
    ok = (st > 0) & (en < n - 1) & ((Tn[en] - T[st]) < tol)
    out = cond.copy()
    for a, b in zip(st[ok], en[ok]):
        out[a:b + 1] = True
        wdip[a:b + 1] = (Tn - T)[a:b + 1]
        dstart[a] = 1.0
    return out, wdip, dstart


def reduce_runs(x, st, en, op):
    """op (maximum/minimum) по x[st[k]:en[k]+1] для каждого участка."""
    idx = np.empty(2 * len(st), np.int64)
    idx[0::2], idx[1::2] = st, en + 1
    return op.reduceat(np.r_[x, x[-1:]], idx)[0::2]


def scan_pair(A, B, t0, t1, thetas=THETAS, tol=None, sim=None):
    """-> dict {eps: {(θ, вид): массив (n, len(COLS))}, nruns: {(θ, вид): участков ≥ SHORT_MS мс}, sim: {(θ, вид): {(d, выход): массив (n, SIM_COLS)}}, …} или None."""
    tol = TOL_MS if tol is None else tol
    sim = DO_SIM if sim is None else sim
    T, S, Bu, ia, ib = core.pair_series(A, B, t1)
    n = len(T)
    if n < 50:
        return None
    ageA, ageB = (T - A["e"][ia]).astype(np.float32), (T - B["e"][ib]).astype(np.float32)
    valid = (ageA <= CAP_MS) & (ageB <= CAP_MS)
    if valid.sum() < 50:
        return None
    mins, _, sb, bb = core.minute_bars(T[valid], S[valid], Bu[valid])
    m0, cen = core.rolling_centre(mins, sb, bb, CENTRE_MIN, CENTRE_MIN // 2)
    cT = cen[np.clip(T // 60000 - m0, 0, len(cen) - 1)]
    mid_min = np.full(len(cen), np.nan)
    mid_min[mins - m0] = (sb[3] + bb[3]) / 2
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        med10 = np.nanmedian(np.lib.stride_tricks.sliding_window_view(np.r_[np.full(10, np.nan), mid_min], 10)[: len(mid_min)], axis=1)   # медиана минут [k−10, k−1]
    inw = T >= t0
    Tn = np.r_[T[1:], t1]
    endA, endB = (Tn - A["e"][ia]).astype(np.float32), (Tn - B["e"][ib]).astype(np.float32)   # возраст ноги к концу интервала (до следующего события): максимум за эпизод
    midA = ((A["bid"][ia] + A["ask"][ia]) / 2).astype(np.float32)
    midB = ((B["bid"][ib] + B["ask"][ib]) / 2).astype(np.float32)
    spA = ((A["ask"][ia] - A["bid"][ia]) / midA * 1e4).astype(np.float32)
    spB = ((B["ask"][ib] - B["bid"][ib]) / midB * 1e4).astype(np.float32)
    size = {1: np.minimum(A["dbid"][ia], B["dask"][ib]).astype(np.float32), -1: np.minimum(A["dask"][ia], B["dbid"][ib]).astype(np.float32)}
    edge = {"z": {1: S, -1: -Bu}, "c": {1: S - cT, -1: cT - Bu}}
    out = {"eps": {}, "nruns": {}, "sim": {}, "mismatch": False}
    M = (S + Bu) / 2
    cache = {}
    cen_ok = np.flatnonzero(inw & ~np.isnan(cT))
    t_first = max(t0, int(T[0]))
    t_cen = int(T[cen_ok[0]]) if len(cen_ok) else t1
    out["start"] = {"z": t_first, "c": t_cen}
    vm = valid & inw
    out["M_med"] = float(np.median(((S + Bu) / 2)[vm])) if vm.any() else None
    if out["M_med"] is not None and abs(out["M_med"]) >= MISMATCH_BPS:
        out["mismatch"] = True
        return out
    out["valid_share"] = float((Tn - T)[vm].sum() / max((Tn - T)[inw].sum(), 1))
    for view in ("z", "c"):
        for th in thetas:
            recs, nshort = [], np.zeros(len(SHORT_MS), np.int64)
            for side in (1, -1):
                with np.errstate(invalid="ignore"):
                    cond = inw & (edge[view][side] >= th)
                if not cond.any():
                    continue
                cond, wdip, dstart = bridge_dips(cond, T, Tn, tol)
                cw, cd = np.r_[0.0, np.cumsum(wdip)], np.r_[0.0, np.cumsum(dstart)]
                st, en = runs_of(cond)
                dur = (Tn[en] - T[st]).astype(float)
                for k, ms in enumerate(SHORT_MS):
                    nshort[k] += int((dur >= ms).sum())
                keep = dur >= MIN_KEEP_MS
                if not keep.any():
                    continue
                st, en, dur = st[keep], en[keep], dur[keep]
                m = len(st)
                ndip, tdip = cd[en + 1] - cd[st], cw[en + 1] - cw[st]
                c0 = cT[st] if view == "c" else np.full(m, np.nan)
                k0 = T[st] // 60000 - m0
                trend = np.where(view == "c", med10[np.clip(k0, 0, len(med10) - 1)] - cT[st], np.nan) * side
                recs.append(np.c_[
                    T[st], dur, np.full(m, side), (en == n - 1).astype(float), st,
                    reduce_runs(endA, st, en, np.maximum), reduce_runs(endB, st, en, np.maximum),
                    ia[en] - ia[st], ib[en] - ib[st], size[side][st], reduce_runs(size[side], st, en, np.minimum),
                    edge[view][side][st], reduce_runs(edge[view][side], st, en, np.maximum), c0, trend, midA[st], midB[st], spA[st], spB[st], en - st + 1, ndip, tdip])
            out["nruns"][(th, view)] = nshort
            eps = np.vstack(recs) if recs else np.zeros((0, len(COLS)))
            eps = eps[np.argsort(eps[:, CI["t"]], kind="stable")]
            out["eps"][(th, view)] = eps
            if len(eps) and sim:
                out["sim"][(th, view)] = {(d, kind): simulate(A, B, T, S, Bu, M, cT, t1, th, view, eps, d, kind, cache) for d in DELAYS for kind in KINDS}
    return out


def exit_next(kind, side, S, Bu, M, cT, view, th, cache):
    """nxt[k] — первый индекс ряда ≥ k, где выполнено условие выхода позиции стороны side (n — нет). Кэш по (выход, сторона, вид, θ) нужен только для x1-t."""
    key = (kind, side, view, th) if kind == "x1-t" else (kind, side)
    if key not in cache:
        n = len(S)
        with np.errstate(invalid="ignore"):
            if kind == "c-t":
                cond = side * (M - cT) <= 0
            elif kind == "z-t":
                cond = side * M <= 0
            else:
                base = cT if view == "c" else 0.0
                cond = (base - Bu >= th) if side == 1 else (S - base >= th)
        idx = np.where(cond, np.arange(n, dtype=np.int32), np.int32(n))
        cache[key] = np.minimum.accumulate(idx[::-1])[::-1]
    return cache[key]


def placebo(A, B, t_lo, t1, tx, te, side, rng, k=PLACEBO_K, fee=FEE_RT_BPS):
    """Плацебо к настоящим кругам: те же пара, сторона и длительность удержания, но вход в СЛУЧАЙНЫЙ момент ряда (равномерно по [t_lo, t1 − удержание]), тейкером на обеих ногах по книге момента,
    выход через то же удержание, та же комиссия — платится тот же спред. k случайных входов на круг; ноги с возрастом лучшей цены > CAP_MS в момент входа или выхода не считаются.
    -> (среднее net по допустимым входам, средний размер вершины $) — массивы длины len(tx), NaN — нет допустимых."""
    m = len(tx)
    hold = (te - tx).astype(np.int64)
    hi = (t1 - hold - 1).astype(float)
    tx2 = (t_lo + rng.random((m, k)) * np.maximum(hi - t_lo, 0.0)[:, None]).astype(np.int64)
    te2 = tx2 + hold[:, None]
    Ae, Be = A["e"], B["e"]
    ia, ib = np.searchsorted(Ae, tx2, "right") - 1, np.searchsorted(Be, tx2, "right") - 1
    ja, jb = np.searchsorted(Ae, te2, "right") - 1, np.searchsorted(Be, te2, "right") - 1
    ok = (ia >= 0) & (ib >= 0) & (ja >= 0) & (jb >= 0) & (hi[:, None] > t_lo)
    ia, ib, ja, jb = (np.maximum(x, 0) for x in (ia, ib, ja, jb))
    ok &= ((tx2 - Ae[ia]) <= CAP_MS) & ((tx2 - Be[ib]) <= CAP_MS) & ((te2 - Ae[ja]) <= CAP_MS) & ((te2 - Be[jb]) <= CAP_MS)
    sd = side[:, None]
    edge = np.where(sd > 0, (A["bid"][ia] / B["ask"][ib] - 1) * 1e4, (A["ask"][ia] / B["bid"][ib] - 1) * 1e4)
    ex = np.where(sd > 0, (A["ask"][ja] / B["bid"][jb] - 1) * 1e4, (A["bid"][ja] / B["ask"][jb] - 1) * 1e4)
    gross = np.where(sd > 0, edge - ex, ex - edge)
    size = np.where(sd > 0, np.minimum(A["dbid"][ia], B["dask"][ib]), np.minimum(A["dask"][ia], B["dbid"][ib]))
    net = np.where(ok, gross - fee, np.nan)
    size = np.where(ok, size, np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmean(net, axis=1), np.nanmean(size, axis=1)


def simulate(A, B, T, S, Bu, M, cT, t1, th, view, eps, d, kind, cache, fee=FEE_RT_BPS, t_lo=None, seed=None):
    """Круги по эпизодам (в порядке времени): вход через d мс после сигнала первого момента эпизода тейкером на обеих ногах по книге момента входа, выход тейкером через d мс после сигнала.
    Позиция занята до исполнения выхода (эпизод с сигналом раньше — пропускается); не закрытая за HOLD_CAP_MS (30 мин) закрывается по рынку (how = 2); не закрытая до t1 — занимает пару до конца.
    Вид «от нуля» + выход «к центру»: вход только если в момент сигнала отклонение от причинного центра ≥ DEV_MIN_BPS (иначе сигнал выхода уже стоит на входе — вырожденный круг).
    Если задан seed — плацебо (случайные входы, см. placebo) пишется в столбцы pl, plsize. -> массив (len(eps), len(SIM_COLS)), NaN — нет круга / не закрыт."""
    n, m = len(T), len(eps)
    out = np.full((m, len(SIM_COLS)), np.nan)
    Ae, Be = A["e"], B["e"]
    t_free = -np.inf
    need_dev = kind == "c-t" and view == "z"
    for j in range(m):
        ts, side = eps[j, CI["t"]], int(eps[j, CI["side"]])
        if ts < t_free:
            continue
        if need_dev:
            i0 = int(eps[j, CI["i0"]])
            dev = (S[i0] - cT[i0]) if side > 0 else (cT[i0] - Bu[i0])
            if not dev >= DEV_MIN_BPS:
                continue
        tx = ts + d
        if tx >= t1:
            continue
        im, ib = int(np.searchsorted(Ae, tx, "right")) - 1, int(np.searchsorted(Be, tx, "right")) - 1
        if im < 0 or ib < 0:
            continue
        edge = (A["bid"][im] / B["ask"][ib] - 1) * 1e4 if side > 0 else (A["ask"][im] / B["bid"][ib] - 1) * 1e4
        size = min(A["dbid"][im], B["dask"][ib]) if side > 0 else min(A["dask"][im], B["dbid"][ib])
        k0 = max(int(np.searchsorted(T, tx, "right")) - 1, 0)
        out[j, [SIM_COLS.index("size"), SIM_COLS.index("tx"), SIM_COLS.index("edge_tx"), SIM_COLS.index("c_tx")]] = (size, tx, edge, cT[k0])
        ke = int(exit_next(kind, side, S, Bu, M, cT, view, th, cache)[k0])
        te = max(int(T[ke]), tx) + d if ke < n else None
        how = 1
        if te is None or te - tx > HOLD_CAP_MS:
            te, how = tx + HOLD_CAP_MS + d, 2
        if te >= t1:
            t_free = np.inf
            continue
        im2, ib2 = int(np.searchsorted(Ae, te, "right")) - 1, int(np.searchsorted(Be, te, "right")) - 1
        gross = edge - (A["ask"][im2] / B["bid"][ib2] - 1) * 1e4 if side > 0 else (A["bid"][im2] / B["ask"][ib2] - 1) * 1e4 - edge
        out[j, [SIM_COLS.index("net"), SIM_COLS.index("gross"), SIM_COLS.index("te"), SIM_COLS.index("how")]] = (gross - fee, gross, te, how)
        t_free = te
    if seed is not None:
        c = np.flatnonzero(np.isfinite(out[:, SIM_COLS.index("net")]))
        if len(c):
            tl = t_lo if t_lo is not None else int(T[0])
            pl, ps = placebo(A, B, tl, t1, out[c, SIM_COLS.index("tx")], out[c, SIM_COLS.index("te")], eps[c, CI["side"]].astype(int), np.random.default_rng(seed), fee=fee)
            out[c, SIM_COLS.index("pl")], out[c, SIM_COLS.index("plsize")] = pl, ps
    return out


def work_coin(coin):
    g = G
    books, mids = {}, {}
    for ex, (sym, mult, meta) in g["coins"][coin].items():
        a = maker_run.load_book(g["bwork"], ex, sym, mult, unit_of(ex, meta))
        if a is None or len(a["e"]) < 20:
            continue
        books[ex] = a
        mids[ex] = float(np.median((a["bid"] + a["ask"]) / 2))
    res, skipped = {}, []
    exs = [e for e in EX5 if e in books]
    tm = time.time()
    for i, a_ in enumerate(exs):
        for b_ in exs[i + 1:]:
            ratio = mids[a_] / mids[b_]
            if not 0.5 < ratio < 2.0:
                skipped.append((coin, a_, b_, ratio, None))
                continue
            r = scan_pair(books[a_], books[b_], g["t0"], g["t1"])
            if r is not None:
                r["ratio"] = ratio
                r["syms"] = (g["coins"][coin][a_][0], g["coins"][coin][b_][0])
                if r["mismatch"]:
                    skipped.append((coin, a_, b_, ratio, r["M_med"]))
                    continue
                res[(coin, a_, b_)] = r
    print(f"{coin} {time.time() - tm:.0f}s pairs {len(res)}", flush=True)
    return res, skipped


def main(bwork, t0, t1, out, par=4):
    days = [time.strftime("%Y-%m-%d", time.gmtime(t0 / 1000 + k * 86400)) for k in range(0, int((t1 - t0) / 86_400_000) + 2)]
    coins, meta = build_coins(str(Path(bwork, "parsed")), days)
    G.update({"bwork": str(Path(bwork, "parsed")), "t0": t0, "t1": t1, "coins": coins})
    names = sorted(coins) if not os.environ.get("COINS") else os.environ["COINS"].split(",")
    t = time.time()
    pairs, skipped, part = {}, [], 0

    def dump():
        nonlocal pairs, skipped, part
        with open(f"{out}.part{part:03d}", "wb") as f:
            pickle.dump({"t0": t0, "t1": t1, "days": day_edges(t0, t1), "cols": COLS, "pairs": pairs, "skipped": skipped, "coins": len(names), "thetas": THETAS}, f, protocol=4)
        pairs, skipped, part = {}, [], part + 1

    with Pool(par, maxtasksperchild=int(os.environ.get("MAXTASKS", "3"))) as p:
        for k, (r, sk) in enumerate(p.imap_unordered(work_coin, names, chunksize=1)):
            pairs.update(r)
            skipped += sk
            if k % 20 == 0:
                print(k, len(names), round(time.time() - t), flush=True)
            if (k + 1) % 50 == 0:
                dump()
    dump()
    print({"coins": len(names), "parts": part, "s": round(time.time() - t, 1)})


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4], int(sys.argv[5]) if len(sys.argv) > 5 else 4)
