"""Инкрементальный счёт вкладки «Арбитраж»: каждый проход дописывает только новые минуты, а не пересчитывает окно 24 ч.

Таблица (эпизоды порога комиссий) совпадает с полным счётом (core.analyze_pair + arb_live.hook) на тех же данных, если ни одна запись не
приходит позже границы прохода (запись с e < t_a отбрасывается и считается в `late`; в боевых данных лаг ≤ 28 с при запасе 60 с).
Отличия от полного счёта (решения, не ошибки):
  * эпизод считается с настоящего начала, а не с начала скользящего окна: когда окно уйдёт от START_MS (через 24 ч), эпизод, начавшийся
    до окна, в статистику не входит (полный счёт на границе окна заводил его заново «с нуля»);
  * пара появляется, когда у обеих сторон ≥ 20 событий; пара, у которой одна сторона накопила 20 событий позже первого прохода, считается
    с того прохода (до него у неё не было истории).
График пары — «как было» (владелец 15.11: «дырки в графике»): состояние стакана живёт между событиями, поэтому слот без единой записи не дыра —
в нём держится последнее известное значение (ступенчатая линия); максимум Sell и минимум Buy слота учитывают и состояние на входе в слот.
Линия рвётся только там, где у одной из двух бирж нет ни одной записи за минуту по всем её символам (настоящий провал сбора, Alive);
до первой записи пары линии нет. Прежний core.chart_slots берёт только минуты с записями — для живой вкладки устарел.
Состояние пары (всё, что нужно для продолжения): плотные ряды минут окна (закрытие середины, максимум Sell, минимум Buy, центр, число строк
с центром), последнее состояние Sell/Buy, закрытые эпизоды порога комиссий, открытый эпизод по каждому направлению (вход, пик на сейчас,
цены входа). Состояние символа: последняя принятая запись до границы (carry) и ещё не обработанные записи (pend).
"""
import warnings

import numpy as np

import core

TH = core.FEE_RT_BPS
NEED_EVENTS = 20            # как в analyze.work_coin: символ с меньшим числом событий пропускается
EMPTY = np.empty(0)


class Sym:
    """Поток символа: pend — сырые записи (e, rx, bid, ask, dep) с e ≥ t_a; carry — последняя принятая (сохранённая после снятия повторов)."""

    __slots__ = ("carry", "pend", "n", "mid", "late")

    def __init__(self):
        self.carry = None       # (e, bid, ask, dep) или None
        self.pend = None        # dict массивов или None
        self.n = 0              # принятых записей всего (для правила «≥ 20»)
        self.mid = None         # медиана середины — для проверки «одна монета» (0,9 < ratio < 1,1)
        self.late = 0           # записей отброшено как слишком поздних (e < границы)

    def ingest(self, e, rx, bid, ask, dep):
        a = {"e": e, "rx": rx, "bid": bid, "ask": ask, "dep": dep}
        if self.pend is None:
            self.pend = a
        else:
            self.pend = {k: np.concatenate([self.pend[k], a[k]]) for k in a}

    def take(self, t_a, t_b):
        """Принятые записи с e в [t_a, t_b): отсортированы, без негодных и повторов; carry обновляется. -> dict(e, bid, ask, dep) или None."""
        p = self.pend
        if p is None or not len(p["e"]):
            return None
        hi = p["e"] < t_b
        if not hi.any():
            return None
        batch = {k: v[hi] for k, v in p.items()}
        self.pend = {k: v[~hi] for k, v in p.items()} if (~hi).any() else None
        old = batch["e"] < t_a
        if old.any():
            self.late += int(old.sum())
            batch = {k: v[~old] for k, v in batch.items()}
            if not len(batch["e"]):
                return None
        o = np.lexsort((batch["rx"], batch["e"]))
        batch = {k: v[o] for k, v in batch.items()}
        ok = (batch["bid"] > 0) & (batch["ask"] > 0) & (batch["bid"] <= batch["ask"])
        if not ok.all():
            batch = {k: v[ok] for k, v in batch.items()}
        e, bid, ask, dep = batch["e"], batch["bid"], batch["ask"], batch["dep"]
        if not len(e):
            return None
        if self.carry is None:
            ch = np.r_[True, (bid[1:] != bid[:-1]) | (ask[1:] != ask[:-1])]
        else:
            pb, pa = self.carry[1], self.carry[2]
            pbid, pask = np.r_[pb, bid[:-1]], np.r_[pa, ask[:-1]]
            ch = (bid != pbid) | (ask != pask)
        if not ch.any():
            return None
        e, bid, ask, dep = e[ch], bid[ch], ask[ch], dep[ch]
        self.carry = (int(e[-1]), float(bid[-1]), float(ask[-1]), float(dep[-1]))
        self.n += len(e)
        return {"e": e, "bid": bid, "ask": ask, "dep": dep}


def with_carry(carry, b):
    """Массивы символа на проход: запись-носитель (последняя до границы) + новые. -> (число носителей 0/1, e, bid, ask, dep); None — данных нет совсем."""
    if b is None:
        return None if carry is None else (1, *(np.array([x]) for x in carry))
    if carry is None:
        return 0, b["e"], b["bid"], b["ask"], b["dep"]
    return 1, np.r_[carry[0], b["e"]], np.r_[carry[1], b["bid"]], np.r_[carry[2], b["ask"]], np.r_[carry[3], b["dep"]]


class Alive:
    """Минуты, в которые у биржи была хоть одна принятая запись (по всем её символам). Нет записей за минуту — сбор не работал (провал)."""

    __slots__ = ("base", "a")

    def __init__(self, base):
        self.base = base        # минута первого элемента
        self.a = {}             # биржа -> bool-массив по минутам base, base+1, ...

    def mark(self, ex, minutes):
        """minutes — массив номеров минут (уникальных или нет): отмечает их живыми."""
        if not len(minutes):
            return
        a = self.a.get(ex)
        need = int(minutes.max()) - self.base + 1
        if a is None:
            a = self.a[ex] = np.zeros(max(need, 0), bool)
        elif len(a) < need:
            a = self.a[ex] = np.r_[a, np.zeros(need - len(a), bool)]
        i = minutes - self.base
        a[i[i >= 0]] = True

    def extend(self, k_to):
        """Все биржи дотягиваются до минуты k_to (не включая): минуты без записей — провал."""
        n = k_to - self.base
        for ex, a in list(self.a.items()):
            if len(a) < n:
                self.a[ex] = np.r_[a, np.zeros(n - len(a), bool)]

    def prune(self, k):
        """Выбрасывает минуты раньше k."""
        d = k - self.base
        if d > 0:
            for ex, a in list(self.a.items()):
                self.a[ex] = a[d:]
            self.base = k

    def ok(self, ea, eb, m0, n):
        """Живые у обеих бирж минуты m0 .. m0+n−1 (bool, длина n); вне известного — не живые."""
        out = np.zeros(n, bool)
        a, b = self.a.get(ea), self.a.get(eb)
        if a is None or b is None:
            return out
        i0 = m0 - self.base
        lo, hi = max(i0, 0), min(i0 + n, len(a), len(b))
        if hi > lo:
            out[lo - i0: hi - i0] = a[lo:hi] & b[lo:hi]
        return out


class Pair:
    __slots__ = ("m0", "mid", "hi", "lo", "cen", "nv", "sc", "bc", "closed", "open", "cache")

    def __init__(self):
        self.m0 = None                          # минута начала плотных рядов (None — рядов ещё нет); ряды идут подряд, минута за минутой
        self.mid = EMPTY                        # (S_close + B_close)/2 минуты; NaN — в минуте не было строк (для центра, как у viking)
        self.hi = EMPTY                         # максимум Sell за минуту с учётом состояния на входе в минуту; NaN — состояния ещё нет
        self.lo = EMPTY                         # минимум Buy за минуту, так же
        self.cen = EMPTY                        # центр минуты (NaN — нет), считается для каждой минуты
        self.nv = np.empty(0, np.int64)         # строк с известным центром и T ≥ START в минуте
        self.sc = self.bc = None                # Sell/Buy на конец последней минуты (состояние переходит в следующую)
        self.closed = []                        # закрытые эпизоды: (T_вход, напр., τ мс, False, пик, круг, глубина)
        self.open = {1: None, -1: None}         # открытый эпизод: [T_вход, S_вход, B_вход, пик, глубина]
        self.cache = None                       # (подпись эпизодов, сводка) — сводка пересчитывается, только когда эпизоды изменились


def ffill(x, init):
    """Последнее не-NaN значение до каждого элемента включительно; до первого — init (None → NaN)."""
    idx = np.where(np.isnan(x), -1, np.arange(len(x)))
    np.maximum.accumulate(idx, out=idx)
    return np.where(idx >= 0, x[np.maximum(idx, 0)], np.nan if init is None else init)


def centres(tail, mid_new):
    """Центр для минут нового куска — как core.rolling_centre: медиана mid закрытий окна [k−60, k−1], NaN при < 30 баров.
    tail — mid последних минут до куска (подряд, не больше 60), mid_new — mid минут куска."""
    pad = np.r_[np.full(core.WIN_MIN - len(tail), np.nan), tail]
    ext = np.r_[pad, mid_new]
    win = np.lib.stride_tricks.sliding_window_view(ext, core.WIN_MIN)[: len(mid_new)]   # окно [k−60, k−1] для минуты k куска
    cnt = np.sum(~np.isnan(win), axis=1)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        c = np.nanmedian(win, axis=1)
    c[cnt < core.NEED_BARS] = np.nan
    return c


def machine(ps, d, Tv, Sv, Bv, cv, depv, e, ent, ex):
    """Один проход автомата входов/выходов одного направления по валидным строкам. Дописывает закрытые эпизоды в ps.closed, обновляет ps.open[d]."""
    k0 = 0
    st = ps.open[d]
    if st is not None:
        xi = np.flatnonzero(ex)
        if not len(xi):
            st[3] = max(st[3], float(e.max()))
            return
        j = int(xi[0])
        pk = max(st[3], float(e[: j + 1].max()))
        gross = float(st[1] - Bv[j]) if d == 1 else float(Sv[j] - st[2])
        ps.closed.append((st[0], d, int(Tv[j] - st[0]), False, pk, gross, st[4]))
        ps.open[d] = None
        k0 = j + 1
        if k0 >= len(Tv):
            return
    for i, j in core.episodes(Tv[k0:], ent[k0:], ex[k0:]):
        i += k0
        if j < 0:
            ps.open[d] = [int(Tv[i]), float(Sv[i]), float(Bv[i]), float(e[i:].max()), float(depv[i])]
            break
        j += k0
        gross = float(Sv[i] - Bv[j]) if d == 1 else float(Sv[j] - Bv[i])
        ps.closed.append((int(Tv[i]), d, int(Tv[j] - Tv[i]), False, float(e[i: j + 1].max()), gross, float(depv[i])))


def advance_pair(ps, A, B, start_ms, k_to):
    """Дописывает к состоянию пары минуты до k_to (не включая). A, B — with_carry(...) сторон (или None, если данных у стороны ещё нет).
    Строки — объединение моментов новых записей (носитель своей строки не создаёт, он лишь даёт состояние стороны на начало);
    start_ms — начало окна статистики (строки раньше греют центр). Минуты без строк получают перенесённое состояние Sell/Buy."""
    T = None
    if A is not None and B is not None:
        nA, tA, bA, aA, dA = A
        nB, tB, bB, aB, dB = B
        T = np.union1d(tA[nA:], tB[nB:])
        if len(T):
            ia = np.searchsorted(tA, T, "right") - 1
            ib = np.searchsorted(tB, T, "right") - 1
            ok = (ia >= 0) & (ib >= 0)
            T, ia, ib = T[ok], ia[ok], ib[ok]
    has_rows = T is not None and len(T) > 0
    if ps.m0 is None:
        if not has_rows:
            return
        k0 = int(T[0] // 60000)
    else:
        k0 = ps.m0 + len(ps.mid)
    if k0 >= k_to:
        return
    L = k_to - k0
    nan = np.full(L, np.nan)
    hi_b, lo_b, cs, cb, mid = nan.copy(), nan.copy(), nan.copy(), nan.copy(), nan.copy()
    if has_rows:
        S = (bA[ia] / aB[ib] - 1.0) * 1e4
        Bu = (aA[ia] / bB[ib] - 1.0) * 1e4
        dep = np.minimum(dA[ia], dB[ib])
        mins, _, sb, bb = core.minute_bars(T, S, Bu)
        ix = mins - k0
        hi_b[ix], lo_b[ix], cs[ix], cb[ix] = sb[1], bb[2], sb[3], bb[3]
        mid[ix] = (sb[3] + bb[3]) / 2
    fs, fb = ffill(cs, ps.sc), ffill(cb, ps.bc)                                   # состояние на конец каждой минуты
    hi = np.fmax(np.r_[np.nan if ps.sc is None else ps.sc, fs[:-1]], hi_b)       # на входе в минуту — состояние конца прошлой
    lo = np.fmin(np.r_[np.nan if ps.bc is None else ps.bc, fb[:-1]], lo_b)
    cen = centres(ps.mid[-core.WIN_MIN:], mid)
    nv = np.zeros(L, np.int64)
    if has_rows:
        c = cen[T // 60000 - k0]
        v = ~np.isnan(c) & (T >= start_ms)
        nv = np.bincount(T[v] // 60000 - k0, minlength=L)
    if ps.m0 is None:
        ps.m0 = k0
    ps.mid, ps.hi, ps.lo, ps.cen, ps.nv = (np.r_[o, n] for o, n in ((ps.mid, mid), (ps.hi, hi), (ps.lo, lo), (ps.cen, cen), (ps.nv, nv)))
    ps.sc = None if np.isnan(fs[-1]) else float(fs[-1])
    ps.bc = None if np.isnan(fb[-1]) else float(fb[-1])
    if has_rows and v.any():
        Tv, Sv, Bv, cv, dv = T[v], S[v], Bu[v], c[v], dep[v]
        Es, Eb, M = Sv - cv, cv - Bv, (Sv + Bv) / 2
        machine(ps, 1, Tv, Sv, Bv, cv, dv, Es, Es >= TH, M <= cv)
        machine(ps, -1, Tv, Sv, Bv, cv, dv, Eb, Eb >= TH, M >= cv)


def prune(ps, t1, window_ms=24 * 3600_000):
    """Выбрасывает минуты старше окна и эпизоды, начавшиеся до окна."""
    if ps.m0 is not None:
        k = min((t1 - window_ms) // 60000 - 1 - ps.m0, len(ps.mid))
        if k > 0:
            ps.mid, ps.hi, ps.lo, ps.cen, ps.nv = (x[k:] for x in (ps.mid, ps.hi, ps.lo, ps.cen, ps.nv))
            ps.m0 += k
    ps.closed = [r for r in ps.closed if r[0] >= t1 - window_ms]


def slots(ps, tmin0, n, alive):
    """Максимум Sell, минимум Buy и центр по слотам SLOT_MIN минут окна, минуты — подряд с tmin0 (значения ×10, целые; None — нет данных).
    Максимум и минимум — по живым минутам слота (alive — bool по минутам рядов пары), центр — последней живой минуты слота."""
    S = core.SLOT_MIN
    out = [np.full(n, np.nan) for _ in range(3)]
    a = max(ps.m0, tmin0)
    i0 = a - ps.m0
    if i0 < len(ps.mid):
        j0 = (a - tmin0) // S
        head = (a - tmin0) - j0 * S                      # минут слота j0 до первой хранимой
        m = len(ps.mid) - i0
        ng = -(-(head + m) // S)

        def pad(x, fill):
            return np.r_[np.full(head, fill), x[i0:], np.full(ng * S - head - m, fill)].reshape(ng, S)

        ok = pad(alive, False)
        hi = np.fmax.reduce(np.where(ok, pad(ps.hi, np.nan), np.nan), axis=1)
        lo = np.fmin.reduce(np.where(ok, pad(ps.lo, np.nan), np.nan), axis=1)
        last = S - 1 - np.argmax(ok[:, ::-1], axis=1)
        cen = np.where(ok.any(axis=1), pad(ps.cen, np.nan)[np.arange(ng), last], np.nan)
        e = min(j0 + ng, n)
        for o, v in zip(out, (hi, lo, cen)):
            o[j0:e] = v[: e - j0]
    return q10(out[0]), q10(out[1]), q10(out[2])


def q10(v):
    """Значения ×10 целыми (округление до чётного, как round), NaN → None."""
    fin = np.isfinite(v)
    out = np.rint(np.where(fin, v, 0.0) * 10).astype(np.int64).tolist()
    for i in np.flatnonzero(~fin):
        out[i] = None
    return out


def result(ps, t0, t1, periods_h, alive=None):
    """Что отдаёт пара в таблицу и график; None — пары нет (центр известен меньше чем в 51 строке окна).
    alive — bool по минутам рядов пары (None — все живые)."""
    if ps.m0 is None or not len(ps.mid):
        return None
    w0 = max(t0 // 60000 - ps.m0, 0)
    if int(ps.nv[w0:].sum()) <= 50:
        return None
    eps = [r for r in ps.closed if r[0] >= t0]
    for d in (1, -1):
        o = ps.open[d]
        if o is not None and o[0] >= t0:
            eps.append((o[0], d, int(t1 - o[0]), True, o[3], None, o[4]))
    ts = np.array([r[0] for r in eps], np.int64)
    cnt = [int((ts >= t1 - h * 3600_000).sum()) for h in periods_h]
    sig = (len(eps), ts.min() if len(ts) else None, ts.max() if len(ts) else None, tuple(ps.open[1] or ()), tuple(ps.open[-1] or ()))
    if getattr(ps, "cache", None) is None or ps.cache[0] != sig:
        ps.cache = (sig, core.ep_table_stats(eps))
    S, B, C = slots(ps, t0 // 60000, int(np.ceil((t1 - t0) / (core.SLOT_MIN * 60000))), np.ones(len(ps.mid), bool) if alive is None else alive)
    return {"cnt": cnt, "big": core.big_counts(eps, t1), "st": ps.cache[1], "S": S, "B": B, "C": C}
