#!/usr/bin/env python3
"""П-42 §13.68 / §13.68.1 (TK-668): признаки и повтор E8 — чистые функции. Диска (кроме чтения кэша `.b42` и ленты) и движка не касаются; тесты — `tests/test_tk668.py`.
Все признаки причинные: значения только из закрытых минут строго до минуты сигнала (`t = sig_ms // 60000`, последняя минута — `t − 1`); знак стороны `s` = +1 у лонга, −1 у шорта.
  R   (E5)  расстояние до следующего живого уровня в сторону сделки, в NATR сигнала (`R = |P_next − P| / (P·natr)`, нет следующего → +∞);
  X24 / X7 / XP / XV (EP)  ход монеты за 24 ч / 7 сут. в сторону сделки, положение в диапазоне 24 ч, отклонение от VWAP 24 ч в NATR;
  bz{h} (B)  шок BTC за h мин в σ: `s·r_h / σ_h`, `r_h = cf[t−1]/cf[t−1−h] − 1`, `σ_h = σ1·√h`, σ1 — ddof 1 минутных доходностей BTC за 1 441 минуту строго до интервала.
E8: `sim_e8` — повтор покупки после выхода `rt` шорта по ленте (механика — §13.68.1 п. 2)."""
from __future__ import annotations

import math

import numpy as np

try:
    from numba import njit
except ImportError:                                   # ПК владельца: тесты малы, без numba
    def njit(*a, **k):
        if a and callable(a[0]):
            return a[0]
        return lambda f: f

MIN_MS = 60_000
DAY_MIN = 1440
WEEK_MIN = 10_080
MIN_VALID_DAY = 1000                                  # §13.68 п. 3: ≥ 1 000 минут с данными в окне 24 ч
MIN_VALID_SIG = 1000                                  # §13.68.1 п. 5: ≥ 1 000 валидных минутных доходностей BTC из 1 441
HORIZONS = (1, 5, 15)
K_MAIN, K_NB = 2.0, (1.5, 3.0)
U32_MAX = 4294967295
NAN = float("nan")
FEATURES = ("R", "X24", "X7", "XP", "XV", "bz1", "bz5", "bz15")


# ---- минутки ----------------------------------------------------------------------------------------------------------------------

def load_b42_full(path):
    """Кэш минуток `.b42` движка: {o, h, l, c, qv} (int64, N минут от T0); o = 0 — минутка без данных."""
    with open(path, "rb") as f:
        head = f.read(8)
    if head[:4] != b"B42\x01":
        raise ValueError("%s: не .b42" % path)
    n = int.from_bytes(head[4:8], "little")
    a = np.fromfile(path, dtype="<i8", offset=8, count=5 * n).reshape(5, n)
    return dict(o=a[0], h=a[1], l=a[2], c=a[3], qv=a[4])


def garbage_from(a, t, rng):
    """Копия минуток с мусором с минуты `t` и дальше (G-causal): случайное блуждание цены от последнего закрытия до `t`, структура бара верна (`o, c ≥ 1`, `h ≥ max(o, c)`, `l ≤ min(o, c)`, `qv > 0`, все минуты с данными)."""
    g = {k: v.copy() for k, v in a.items()}
    m = len(a["o"]) - t
    if m <= 0:
        return g
    last = ffill_close(a["o"][:t], a["c"][:t])[-1] if t > 0 else np.nan
    scale = float(last) if np.isfinite(last) and last > 0 else 1e6
    path = scale * np.exp(np.cumsum(rng.normal(0.0, 0.02, m)))
    o = path * np.exp(rng.normal(0.0, 0.01, m))
    c = path * np.exp(rng.normal(0.0, 0.01, m))
    h = np.maximum(o, c) * np.exp(np.abs(rng.normal(0.0, 0.005, m)))
    l = np.minimum(o, c) * np.exp(-np.abs(rng.normal(0.0, 0.005, m)))
    r = lambda x: np.clip(np.rint(x), 1, 4e18).astype(np.int64)  # noqa: E731
    g["o"][t:], g["c"][t:] = r(o), r(c)
    g["h"][t:] = np.maximum(r(h), np.maximum(g["o"][t:], g["c"][t:]))
    g["l"][t:] = np.minimum(r(l), np.minimum(g["o"][t:], g["c"][t:]))
    g["qv"][t:] = rng.integers(1, 10**9, m)
    return g


def ffill_close(o, c):
    """Последнее закрытие с данными (float64), до первой минутки с данными — NaN (как `tk656_run.ffill_close`)."""
    cf = np.where(o != 0, c.astype(np.float64), np.nan)
    idx = np.where(o != 0, np.arange(len(cf)), -1)
    np.maximum.accumulate(idx, out=idx)
    return np.where(idx >= 0, cf[np.maximum(idx, 0)], np.nan)


class Mins:
    """Подготовленные ряды монеты: ffill-закрытие, ряды для max / min, префиксные суммы для VWAP и числа минут с данными."""

    def __init__(self, a):
        o = a["o"]
        valid = o != 0
        self.n = len(o)
        self.cf = ffill_close(o, a["c"])
        self.hh = np.where(valid, a["h"].astype(np.float64), -np.inf)
        self.ll = np.where(valid, a["l"].astype(np.float64), np.inf)
        self.nv = np.concatenate([[0], np.cumsum(valid)])
        tp = (a["h"].astype(np.float64) + a["l"].astype(np.float64) + a["c"].astype(np.float64)) / 3.0
        qv = a["qv"].astype(np.float64)
        ok = valid & (tp > 0)
        self.P1 = np.concatenate([[0.0], np.cumsum(np.where(valid, qv, 0.0))])
        self.P2 = np.concatenate([[0.0], np.cumsum(np.where(ok, qv / np.where(ok, tp, 1.0), 0.0))])


def place_features(m, t, s, natr):
    """(X24, X7, XP, XV) на минуте `t` для знака `s` и NATR (доля, `natr_e8 / 1e8`); пусто — NaN.
    Окно 24 ч — минуты `t − 1440 … t − 1` (1 441, как `H = max h[t−1440…t−1]`), нужно ≥ 1 000 с данными."""
    nan4 = (NAN, NAN, NAN, NAN)
    a, b = t - DAY_MIN, t - 1
    if a - 1 < 0 or b >= m.n or not (natr > 0):
        return nan4
    if m.nv[b + 1] - m.nv[a] < MIN_VALID_DAY:
        return nan4
    c = m.cf[b]
    c24 = m.cf[a - 1]
    x24 = s * (c / c24 - 1.0) if (np.isfinite(c) and np.isfinite(c24) and c24 != 0) else NAN
    x7 = NAN
    if t - 1 - WEEK_MIN >= 0:
        c7 = m.cf[t - 1 - WEEK_MIN]
        if np.isfinite(c) and np.isfinite(c7) and c7 != 0:
            x7 = s * (c / c7 - 1.0)
    hi, lo = m.hh[a:b + 1].max(), m.ll[a:b + 1].min()
    xp = NAN
    if np.isfinite(c) and hi > lo:
        xp = (c - lo) / (hi - lo) if s > 0 else (hi - c) / (hi - lo)
    s1, s2 = m.P1[b + 1] - m.P1[a], m.P2[b + 1] - m.P2[a]
    xv = NAN
    if np.isfinite(c) and s1 > 0 and s2 > 0:
        xv = s * (c / (s1 / s2) - 1.0) / natr
    return x24, x7, xp, xv


# ---- шок BTC ----------------------------------------------------------------------------------------------------------------------

def minute_returns(cf):
    """Минутные доходности `cf[i]/cf[i−1] − 1` (NaN, где любое из двух закрытий не определено; i = 0 — NaN)."""
    r = np.full(len(cf), np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        r[1:] = cf[1:] / cf[:-1] - 1.0
    return r


def btc_shock(cf, r, t, h):
    """(r_h, σ_h) на минуте `t`: `r_h = cf[t−1]/cf[t−1−h] − 1`; σ1 — станд. откл. (ddof 1) минутных доходностей для `i ∈ [t−1−h−1440, t−1−h]` (строго до начала проверяемого интервала),
    `σ_h = σ1·√h`; нужно ≥ 1 000 конечных доходностей; иначе (NaN, NaN)."""
    a, b = t - 1, t - 1 - h
    lo = b - DAY_MIN
    if lo < 0 or a >= len(cf):
        return NAN, NAN
    w = r[lo:b + 1]
    w = w[np.isfinite(w)]
    if len(w) < MIN_VALID_SIG or not (np.isfinite(cf[a]) and np.isfinite(cf[b]) and cf[b] != 0):
        return NAN, NAN
    return float(cf[a] / cf[b] - 1.0), float(w.std(ddof=1) * math.sqrt(h))


# ---- уровни (E5) ------------------------------------------------------------------------------------------------------------------

class SymLevels:
    """Уровни одной монеты из дампа `P42_LEVELS`: рождение, конец (u32::MAX — до конца данных), версии цены (минута, |цена|)."""

    def __init__(self):
        self.born, self.end, self.segm, self.segp = [], [], [], []
        self.b = self.e = None

    def add(self, born, end, segs):
        self.born.append(born)
        self.end.append(end)
        self.segm.append(np.array([m for m, _ in segs], dtype=np.int64))
        self.segp.append(np.array([abs(p) for _, p in segs], dtype=np.int64))

    def freeze(self):
        self.b = np.array(self.born, dtype=np.int64)
        self.e = np.array(self.end, dtype=np.int64)

    def px_at(self, i, t):
        j = int(np.searchsorted(self.segm[i], t, side="right")) - 1
        return int(self.segp[i][j]) if j >= 0 else None

    def alive(self, i, t):
        return bool(self.b[i] <= t < self.e[i])


def parse_levels(path):
    """{монета: SymLevels} из `<side>-levels.csv` (`sym,level,born,end,first_bar,segs,members`; члены содержат запятые — делим первые 6 полей). Номер уровня = индекс."""
    out = {}
    with open(path, encoding="utf-8") as f:
        next(f)
        for line in f:
            sym, lev, born, end, _fb, segs, _rest = (line.rstrip("\n").split(",", 6) + [""])[:7]
            sl = out.setdefault(sym, SymLevels())
            if int(lev) != len(sl.born):
                raise ValueError("%s: номер уровня %s ≠ %d" % (sym, lev, len(sl.born)))
            sl.add(int(born), int(end), [(int(x.split(":")[0]), int(x.split(":")[1])) for x in segs.split("|") if x])
    for sl in out.values():
        sl.freeze()
    return out


def room_to_next(sl, level, t, p, side_sign, natr):
    """(R, P_next | None, найден ли свой уровень живым с ценой версии = `p`): `side_sign` = +1 лонг (ближайший выше p), −1 шорт (ближайший ниже p); живой — `born ≤ t < end`;
    `R = |P_next − P|/(P·natr)`, нет следующего → +∞ (§13.68 п. 3, §13.68.1 п. 4: цены реальные, положительные)."""
    own = sl.alive(level, t) and sl.px_at(level, t) == p
    best = None
    for j in np.nonzero((sl.b <= t) & (t < sl.e))[0]:
        if j == level:
            continue
        px = sl.px_at(int(j), t)
        if px is None:
            continue
        if side_sign > 0 and px > p and (best is None or px < best):
            best = px
        elif side_sign < 0 and px < p and (best is None or px > best):
            best = px
    if best is None:
        return math.inf, None, own
    return abs(best - p) / (p * natr), best, own


# ---- E8: повтор по ленте -----------------------------------------------------------------------------------------------------------

CODE_OK, CODE_NOTAPE, CODE_E_LE_S = 0, -1, -2
K_TAKE, K_STOP, K_TIME, K_EOD = 0, 1, 2, 3
SLIP = 1e-4
TAKER, MAKER = 3.15e-4, 1.26e-4
STAKE = 500.0


@njit(cache=True)
def sim_e8(t, p, T, lp, natr, stop_kind, stop_par, tk_mult, time_ms):
    """Покупка после выхода `rt` шорта (§13.68.1 п. 2). `t` (мс) и `p` — лента по возрастанию времени; `T` — абсолютное время входа (T0 + exit_ms + 4).
    Возврат: (код, вход_мс, выход_мс, e, цена_выхода, вид, спорная_сделка).
    код: 0 — сделка, −1 — нет ленты после T, −2 — `e ≤ S` (не исполнена); вид: 0 тейк, 1 стоп, 2 время, 3 закрыта концом данных.
    Вход — первая сделка со временем ≥ T, `e = p·(1 + 1 бп)`. Стоп: `stop_kind` 0 — `S = lp·(1 − stop_par)`, 1 — `S = e·(1 − stop_par·natr)`; триггер — первая сделка с ценой ≤ S, исполнение —
    первая сделка со временем ≥ триггер + 4 мс по `p·(1 − 1 бп)` (taker). Тейк `Tk = e·(1 + tk_mult·natr)`: лимит с момента входа, исполняется первой сделкой строго выше `Tk` по цене `Tk` (maker),
    в том числе между триггером стопа и его исполнением. Время: триггер — первая сделка ≥ вход + `time_ms`, исполнение — как у стопа. Спорная сделка — та, на которой исполнение выхода и условие тейка совпали
    (выход идёт первым; число печатается)."""
    n = len(t)
    i0 = np.searchsorted(t, T)
    if i0 >= n:
        return CODE_NOTAPE, 0, 0, 0.0, 0.0, 0, 0
    e = p[i0] * (1.0 + SLIP)
    entry_ms = t[i0]
    if stop_kind == 0:
        S = lp * (1.0 - stop_par)
    else:
        S = e * (1.0 - stop_par * natr)
    if e <= S:
        return CODE_E_LE_S, entry_ms, 0, e, 0.0, 0, 0
    Tk = e * (1.0 + tk_mult * natr)
    t_time = entry_ms + time_ms
    pend_due = -1
    pend_kind = -1
    amb = 0
    for j in range(i0 + 1, n):
        ms, px = t[j], p[j]
        if pend_due >= 0:
            if ms >= pend_due:
                if px > Tk:
                    amb = 1
                return CODE_OK, entry_ms, ms, e, px * (1.0 - SLIP), pend_kind, amb
            if px > Tk:
                return CODE_OK, entry_ms, ms, e, Tk, K_TAKE, 0
            continue
        if px > Tk:
            return CODE_OK, entry_ms, ms, e, Tk, K_TAKE, 0
        if px <= S:
            pend_due, pend_kind = ms + 4, K_STOP
        elif ms >= t_time:
            pend_due, pend_kind = ms + 4, K_TIME
    return CODE_OK, entry_ms, t[n - 1], e, p[n - 1] * (1.0 - SLIP), K_EOD, 0


def e8_money(code, e, xp, kind):
    """(gross, fees) в $ при ставке 500 $: `gross = 500·(xp − e)/e`, `fees = 500·3,15 бп (вход) + 500·(xp/e)·(1,26 бп у тейка / 3,15 бп у остальных)`."""
    if code != CODE_OK:
        return 0.0, 0.0
    return STAKE * (xp - e) / e, STAKE * TAKER + STAKE * (xp / e) * (MAKER if kind == K_TAKE else TAKER)
