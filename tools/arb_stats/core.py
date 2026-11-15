"""Ядро статистики арбитража между биржами: пара (A, B) по лучшим bid/ask на уровне событий (мс).

Формулы раздвижки — как в viking (strategies/xarb_base.md §2; viking-docs p.calc_method, p.sell):
  Sell = (bid_A / ask_B − 1)·10⁴ bps — продать A по биду, купить B по оферу (исполнимо сразу);
  Buy  = (ask_A / bid_B − 1)·10⁴ bps — купить A по оферу, продать B по биду.
Центр и разброс — viking tools/dashboard.py: center_tw (медиана (Sell+Buy)/2), vol_tw (p90 |середина − центр|), авто-центр
(медиана по минутным барам за окно 60 мин, нужно ≥ max(3, окно/2) баров). Уровни — LEVELS viking (5…30 bps), плюс порог комиссий.
"""
import warnings
from array import array

import numpy as np

FEE_RT_BPS = 12.6                                # 4 тейкерских ноги × 3,5 bps × 0,9 (preferences п.12, В-47/71/88)
LEVELS = [5.0, 10.0, FEE_RT_BPS, 15.0, 20.0, 30.0]   # bps: уровни viking + порог комиссий
STALE_MS = 60_000                                # нога без записи книги дольше 60 с — котировки нет: строки пары в это время не создаются (иначе дыра сбора выглядит расхождением)
THETAS = (10.0, 15.0, 20.0, 30.0, 50.0)          # bps: пороги θ кругов «до нуля» и «из минуса в плюс» (0,1 / 0,15 / 0,2 / 0,3 / 0,5 %); на странице выбирается один
CAP_MS = 60_000                                  # вес интервала не больше 60 с (viking spread_stats)
WIN_MIN = 60                                     # окно центра, мин (viking: авто-центр по умолчанию 60)
NEED_BARS = 30                                   # viking: need = max(3, окно // 2)
HIST_LO, HIST_STEP, HIST_N = -300.0, 0.1, 6000   # гистограмма с шагом 0,1 bps на [−300; +300]
SLOT_MIN = 3                                     # слот графика пары, мин
TAU_EDGES_MS = [100, 500, 1000, 5000, 10000, 30000, 60000, 300000, 900000, 3600000]   # границы корзин времени схождения
EDGE_X = [0.0, 0.5, 1, 2, 3, 5, 7.5, 10, 12.6, 15, 20, 30, 50, 100, 200]               # пороги «запас ≥ x bps» (доля времени)


def clean(a):
    """Сортировка по (E, приём), снятие негодных записей и подряд идущих повторов цены. a — dict массивов.
    Запись после паузы > STALE_MS остаётся и при той же цене (книга была без записей — пауза видна). hold[i] — время последней сырой записи серии, которую открыла запись i:
    котировка записи i живёт до hold[i] + STALE_MS (pair_series)."""
    o = np.lexsort((a["rx"], a["e"]))
    a = {k: v[o] for k, v in a.items()}
    ok = (a["bid"] > 0) & (a["ask"] > 0) & (a["bid"] <= a["ask"])
    a = {k: v[ok] for k, v in a.items()}
    e = a["e"]
    ch = np.ones(len(e), bool)
    if len(e) > 1:
        ch[1:] = (a["bid"][1:] != a["bid"][:-1]) | (a["ask"][1:] != a["ask"][:-1]) | (np.diff(e) > STALE_MS)
    idx = np.flatnonzero(ch)
    hold = np.r_[e[idx[1:] - 1], e[-1:]]
    a = {k: v[ch] for k, v in a.items()}
    a["hold"] = hold
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


def pair_series(A, B, t1, stale_ms=STALE_MS):
    """Объединённый ряд по моментам изменения любой из двух сторон: (T, S, Bu, ia, ib). Только где есть обе стороны и у каждой за последние STALE_MS была запись книги
    (A["hold"] — время последней сырой записи серии; нет — берётся время самой записи). stale_ms=None — без ограничения (прежнее поведение: книга переносится вперёд навсегда;
    так считают исследовательские модули maker / outlier, чьи отчёты и тесты на этом стоят)."""
    tA, tB = A["e"], B["e"]
    T = np.union1d(tA, tB)
    ia = np.searchsorted(tA, T, "right") - 1
    ib = np.searchsorted(tB, T, "right") - 1
    ok = (ia >= 0) & (ib >= 0) & (T < t1)
    T, ia, ib = T[ok], ia[ok], ib[ok]
    if stale_ms is not None:
        ok = (T - A.get("hold", tA)[ia] <= stale_ms) & (T - B.get("hold", tB)[ib] <= stale_ms)
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
    """Полная статистика пары за окно [t0, t1) мс. Возвращает (строка, бары, эпизоды по порогам, гистограммы) или None, если данных мало.
    В эпизодах, кроме порогов LEVELS, ключ "circ" — Circles: круги «до нуля» и «из минуса в плюс» по THETAS."""
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
        circ = Circles()
        circ.step(Tv, Sv, Bv, cv)
        res_eps["circ"] = circ
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


class Acc:
    """Закрытые круги: время входа (мс) и валовый итог (bps, до комиссий). Компактно: 16 байт на круг."""

    __slots__ = ("t", "g")

    def __init__(self):
        self.t = array("q")
        self.g = array("d")

    def add(self, t, g):
        self.t.append(t)
        self.g.append(g)

    def arr(self):
        return np.frombuffer(self.t, np.int64), np.frombuffer(self.g, np.float64)

    def prune(self, cut):
        """Выбрасывает круги, вошедшие раньше cut. -> True, если что-то выброшено."""
        t, g = self.arr()
        k = t >= cut
        if k.all():
            return False
        self.t, self.g = array("q", t[k].tolist()), array("d", g[k].tolist())
        return True


def _next(ix, k):
    """Первый элемент отсортированного ix, не меньший k; −1 — нет."""
    p = int(np.searchsorted(ix, k, "left"))
    return int(ix[p]) if p < len(ix) else -1


def zero_step(op, d, Tv, Sv, Bv, ei, xi, acc):
    """Круги «до нуля» одного направления по строкам куска: вход — первая строка из ei (отклонение исполнимой цены от центра ≥ θ), выход — первая строка из xi после входа
    (середина вернулась к центру); валовый итог = Sell входа − Buy выхода (d = 1) или Sell выхода − Buy входа (d = −1). op — открытый круг [T_вход, Sell, Buy] с прошлого куска
    или None. Закрытые — в acc (время входа, итог). -> новый op."""
    k = 0
    if op is not None:
        if not len(xi):
            return op
        j = int(xi[0])
        acc.add(op[0], float(op[1] - Bv[j]) if d == 1 else float(Sv[j] - op[2]))
        op, k = None, int(np.searchsorted(ei, j, "right"))
    while k < len(ei):
        i = int(ei[k])
        q = int(np.searchsorted(xi, i, "right"))
        if q >= len(xi):
            return [int(Tv[i]), float(Sv[i]), float(Bv[i])]
        j = int(xi[q])
        acc.add(int(Tv[i]), float(Sv[i] - Bv[j]) if d == 1 else float(Sv[j] - Bv[i]))
        k = int(np.searchsorted(ei, j, "right"))
    return None


def cross_step(st, e1, e2, up, dn, Tv, Sv, Bv, acc):
    """Круг «из минуса в плюс» (метод владельца): вход, когда исполнимая цена ушла от центра на θ (e1 — Sell ≥ c + θ, позиция +1: продали A / купили B; e2 — Buy ≤ c − θ, позиция −1),
    выход — когда исполнимая цена ДРУГОЙ стороны ушла от центра на θ в противоположную сторону (позиция +1 закрывается на e2, −1 — на e1); итог до комиссий = Sell − Buy по входу и выходу.
    После выхода новый вход — только когда середина вернулась через центр (up: середина ≥ центра после выхода из позиции +1; dn: ≤ центра после −1), иначе выход открыл бы обратную позицию
    той же сделкой («переворот»). e1, e2, up, dn — индексы строк куска. st = [режим, сторона, T_вход, Sell_вход, Buy_вход] меняется на месте;
    режим 0 — готов к входу, 1 / 2 — ждёт возврата середины сверху (после +1) / снизу (после −1), 3 — в позиции."""
    mode, side, t_in, s_in, b_in = st
    n, k = len(Tv), 0
    while k < n:
        if mode in (1, 2):
            r = _next(up if mode == 1 else dn, k)
            if r < 0:
                break
            k, mode = r, 0
        if mode == 0:
            i1, i2 = _next(e1, k), _next(e2, k)
            if i1 < 0 and i2 < 0:
                break
            if i2 < 0 or (i1 >= 0 and i1 < i2):
                side, i = 1, i1
            else:
                side, i = -1, i2
            t_in, s_in, b_in = int(Tv[i]), float(Sv[i]), float(Bv[i])
            mode, k = 3, i
        j = _next(e2 if side == 1 else e1, k)
        if j < 0:
            break
        acc.add(t_in, float(s_in - Bv[j]) if side == 1 else float(Sv[j] - b_in))
        mode, k = (1 if side == 1 else 2), j
    st[:] = [mode, side, t_in, s_in, b_in]


class Circles:
    """Круги по порогам THETAS для пары: «до нуля» (вход при отклонении ≥ θ, выход при возврате середины к центру — как эпизоды порога комиссий, но порог θ) и «из минуса в плюс»
    (cross_step). Исполнение и комиссия — как у столбца «после комиссий»: вход и выход по исполнимой цене в момент сигнала, без задержки, минус FEE_RT_BPS.
    step() принимает строки с известным центром по порядку времени и продолжает с прошлого куска (полный счёт — один кусок, инкрементальный — по проходам)."""

    __slots__ = ("zop", "zacc", "xst", "xacc", "ver")

    def __init__(self):
        self.zop = {th: [None, None] for th in THETAS}              # открытый круг «до нуля»: направление +1 / −1
        self.zacc = {th: Acc() for th in THETAS}
        self.xst = {th: [0, 0, 0, 0.0, 0.0] for th in THETAS}
        self.xacc = {th: Acc() for th in THETAS}
        self.ver = 0                                                # меняется, когда меняются закрытые круги (сводку пересчитывать только тогда)

    def step(self, Tv, Sv, Bv, cv):
        if not len(Tv):
            return
        Es, Eb, Dm = Sv - cv, cv - Bv, (Sv + Bv) / 2 - cv
        up, dn = np.flatnonzero(Dm >= 0), np.flatnonzero(Dm <= 0)      # середина не ниже / не выше центра: выход «до нуля» позиций −1 / +1 и разрешение нового входа после «из минуса в плюс»
        for th in THETAS:
            na, nb = len(self.zacc[th].t), len(self.xacc[th].t)
            e1, e2 = np.flatnonzero(Es >= th), np.flatnonzero(Eb >= th)
            zo = self.zop[th]
            zo[0] = zero_step(zo[0], 1, Tv, Sv, Bv, e1, dn, self.zacc[th])
            zo[1] = zero_step(zo[1], -1, Tv, Sv, Bv, e2, up, self.zacc[th])
            cross_step(self.xst[th], e1, e2, up, dn, Tv, Sv, Bv, self.xacc[th])
            if len(self.zacc[th].t) != na or len(self.xacc[th].t) != nb:
                self.ver += 1

    def prune(self, cut):
        for acc in (*self.zacc.values(), *self.xacc.values()):
            if acc.prune(cut):
                self.ver += 1

    def stats(self):
        """-> (z, x): по THETAS [медиана, p95 круга минус комиссии (bps), число закрытых кругов] или None, если кругов нет."""
        def one(acc):
            t, g = acc.arr()
            if not len(g):
                return None
            net = g - FEE_RT_BPS
            return [float(np.median(net)), float(np.percentile(net, 95)), int(len(g))]
        return [one(self.zacc[th]) for th in THETAS], [one(self.xacc[th]) for th in THETAS]


BIG_BPS = (30.0, 50.0, 100.0)   # «выбросы»: пик эпизода ≥ 0,3 / 0,5 / 1 % (эпизод порога комиссий с таким пиком — это эпизод порога θ, выход тот же)


def big_counts(rows, t1):
    """Число эпизодов порога комиссий с началом за последние 24 ч, пик которых достиг BIG_BPS (rows — как в ep_table_stats)."""
    t = np.array([r[0] for r in rows], np.int64)
    pk = np.array([r[4] for r in rows], float)
    k = t >= t1 - 86_400_000
    return [int((k & (pk >= th)).sum()) for th in BIG_BPS]


def ep_table_stats(rows, fee=FEE_RT_BPS):
    """Сводка эпизодов одной пары для таблицы вкладки: rows — эпизоды порога комиссий (как в analyze_pair).
    -> {n, pk: [медиана, p95, макс] пика, tau: [медиана, p95] времени схождения (с, по вышедшим), net: [медиана, p95] круга минус комиссии (bps)};
    поле None, если данных нет. Круг считается только по вышедшим эпизодам."""
    if not rows:
        return {"n": 0, "pk": None, "tau": None, "net": None}
    pk = np.array([r[4] for r in rows], float)
    tau = np.array([r[2] for r in rows if not r[3]], float) / 1000
    gr = np.array([r[5] for r in rows if r[5] is not None], float) - fee
    return {"n": len(rows), "pk": [float(np.median(pk)), float(np.percentile(pk, 95)), float(pk.max())],
            "tau": [float(np.median(tau)), float(np.percentile(tau, 95))] if len(tau) else None,
            "net": [float(np.median(gr)), float(np.percentile(gr, 95))] if len(gr) else None}


def chart_slots(b, tmin0, n):
    """Максимум Sell, минимум Buy и центр (последний) по слотам SLOT_MIN минут (значения ×10, целые; None — нет данных)."""
    mins = b["m0"] + b["mins"]
    sl = (mins - tmin0) // SLOT_MIN
    ok = (sl >= 0) & (sl < n)
    mins, sl = mins[ok], sl[ok]
    hi, lo, cen = np.full(n, -np.inf), np.full(n, np.inf), np.full(n, np.nan)
    np.maximum.at(hi, sl, b["S"][1][ok])
    np.minimum.at(lo, sl, b["B"][2][ok])
    c = b["centre"]
    cen[sl] = c[np.clip(mins - b["centre_m0"], 0, len(c) - 1)]   # бары идут по возрастанию — в слоте остаётся последний
    has = np.isfinite(hi)
    q = lambda v: [int(round(x * 10)) if f else None for x, f in zip(v, np.isfinite(v))]  # noqa: E731
    return q(np.where(has, hi, np.nan)), q(np.where(has, lo, np.nan)), q(cen)


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
