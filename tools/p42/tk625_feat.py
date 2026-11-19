#!/usr/bin/env python3
"""П-42 §13.32 (TK-625): признак «число минимально успешных пробоев монеты за D суток» — события из `tk625_events.py` → причинные счётчики на кандидате, таблицы `--ftab`,
покрытие и распределение (без P&L), пороги клеток из распределения. Только numpy и stdlib; движок не запускает.

Событие — пересечение уровня (первая сделка за уровнем в 60 мин после сигнала подхода), исход при m·NATR (`o` успех / `f` возврат к уровню раньше / `s` ни то ни другое за 60 мин после пересечения).
Событие закрыто в момент `t_res + L` (L = 5 мин): ему можно входить в признак на кандидате со временем `t ≥ t_res + L`. Окно D суток: пересечение в (t − D, t]. Дубли (одно пересечение, увиденное из
двух эпизодов одного уровня) схлопываются по ключу (сторона, монета, время пересечения); берётся эпизод с наименьшим sig.
Значение определено, только если окно [t − D, t] целиком в накопленной истории: начало ≥ `HIST_START` (24.06 00:00 UTC — до этого уровни движка без полной недельной истории пивотов, §13.26 п. 12),
после первых полных суток торгов монеты и без пропущенных суток сделок монеты в окне. Иначе — пусто (кандидат проходит фильтр).
"""
from __future__ import annotations

import csv
import dataclasses
import datetime

import numpy as np

import tk604_feat as tf
import tk604_stats as st

MIN_MS = 60_000
DAY_MS = st.DAY_MS
L_MIN = 5
L_MS = L_MIN * MIN_MS
BIG = tf.BIG
MS_NAMES = ("05", "10", "20")                 # m = 0,5 / 1 / 2 NATR
DS = (7, 14, 30)
DEF_TAG = "def2"                                # версия определения признака (§13.32 п. 2, правка критика 20.11: возраст листинга); результаты других версий читать запрещено
REASONS = ("определено", "окно до 24.06", "нет данных монеты", "возраст листинга (< D суток собственной истории)", "пропущены сутки сделок")
EPOCH = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
HIST_START_MS = int(datetime.datetime(2026, 6, 24, tzinfo=datetime.timezone.utc).timestamp() * 1000)


def day_index(ms):
    return np.asarray(ms, dtype=np.int64) // DAY_MS


def col_name(m, d):
    return "ok%s_d%d" % (m, d)


COLS = [col_name(m, d) for m in MS_NAMES for d in DS]


# ---- загрузка и склейка ------------------------------------------------------------------------------------------------------

@dataclasses.dataclass
class Events:
    """Эпизоды окна (все) со сделанными исходами; времена — абсолютные мс эпохи."""
    side: np.ndarray
    sym: np.ndarray
    sig: np.ndarray
    ok: np.ndarray                 # ok_ev
    indep: np.ndarray
    cross: np.ndarray              # −1 нет пересечения
    code: dict                     # m → массив кодов 'o'/'f'/'s'/''
    tres: dict                     # m → абсолютное время исхода (−1 нет)

    def __len__(self):
        return len(self.sig)

    def take(self, mask):
        return Events(self.side[mask], self.sym[mask], self.sig[mask], self.ok[mask], self.indep[mask], self.cross[mask],
                      {m: v[mask] for m, v in self.code.items()}, {m: v[mask] for m, v in self.tres.items()})


def read_events_csv(path, t0_ms):
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    n = len(rows)
    side = np.array([r["side"] for r in rows], dtype=object)
    sym = np.array([r["sym"] for r in rows], dtype=object)
    sig = np.array([int(r["sig_ms"]) for r in rows], dtype=np.int64) + t0_ms
    ok = np.array([int(r["ok_ev"]) for r in rows], dtype=np.int8)
    indep = np.array([int(r["indep"]) for r in rows], dtype=np.int8)
    cross = np.array([int(r["cross_ms"]) + t0_ms if r["cross_ms"] != "" else -1 for r in rows], dtype=np.int64)
    code = {m: np.array([r["r" + m] for r in rows], dtype=object) for m in MS_NAMES}
    tres = {m: np.array([int(r["t" + m]) + t0_ms if r["t" + m] != "" else -1 for r in rows], dtype=np.int64) for m in MS_NAMES}
    if n == 0:
        sig = sig.astype(np.int64)
    return Events(side, sym, sig, ok, indep, cross, code, tres)


def splice_events(per_window, windows=st.HIST_WINDOWS):
    """Склейка окон по зачётным диапазонам сигнала (как `tk604_feat.splice_episodes`); охрана G6: ни одного времени (сигнал, пересечение, исход) позже 13.11."""
    st.check_isolation(windows)
    parts, prev_hi = [], None
    for w in windows:
        if prev_hi is not None and w.lod <= prev_hi:
            raise st.GuardError("события: диапазон %s (%s) наезжает на предыдущий (до %s)" % (w.name, w.lo, prev_hi))
        prev_hi = w.hid
        e = per_window[w.name]
        st.check_abs_ms(e.sig, "время сигнала события окна %s" % w.name)
        parts.append(e.take(tf.in_range(e.sig, w)))
    out = Events(*(np.concatenate([getattr(p, f.name) for p in parts]) if f.name not in ("code", "tres") else {m: np.concatenate([getattr(p, f.name)[m] for p in parts]) for m in MS_NAMES}
                   for f in dataclasses.fields(Events)))
    st.check_abs_ms(out.sig, "время сигнала события")
    st.check_abs_ms(out.cross[out.cross >= 0], "время пересечения")
    for m in MS_NAMES:
        st.check_abs_ms(out.tres[m][out.tres[m] >= 0], "время исхода")
    return out


def dedup_crossings(ev):
    """Только пригодные (ok_ev = 1) пересечения; одно пересечение (сторона, монета, время) — одно событие, берётся наименьший sig. Возвращает Events."""
    m = (ev.ok == 1) & (ev.cross >= 0)
    idx = np.nonzero(m)[0]
    order = idx[np.lexsort((ev.sig[idx], ev.cross[idx], ev.sym[idx], ev.side[idx]))]
    seen, keep = set(), []
    for i in order:
        key = (ev.side[i], ev.sym[i], int(ev.cross[i]))
        if key in seen:
            continue
        seen.add(key)
        keep.append(i)
    keep = np.array(sorted(keep), dtype=np.int64)
    mask = np.zeros(len(ev), dtype=bool)
    mask[keep] = True
    return ev.take(mask)


# ---- история ------------------------------------------------------------------------------------------------------------------

class Counter:
    """Счётчики событий одного класса (`o`/`f`/`s`) одной стороны при одном m: по монете, закрытие `t_res + L` ≤ t и пересечение в (t − D, t]."""

    def __init__(self, sym, cross, tres, names):
        self.ix = {s: i for i, s in enumerate(names)}
        sid = np.array([self.ix[s] for s in sym], dtype=np.int64)
        close = tres + L_MS
        if len(close) and int(max(close.max(), cross.max())) >= BIG:
            raise st.GuardError("Counter: время ≥ 2^42")
        self.close = np.sort(sid * BIG + close)
        self.cross = np.sort(sid * BIG + cross)

    def count(self, sym, t_ms, d_days):
        sym = np.asarray(sym, dtype=object)
        t = np.asarray(t_ms, dtype=np.int64)
        sid = np.array([self.ix.get(s, -1) for s in sym], dtype=np.int64)
        known = sid >= 0
        s0 = np.where(known, sid, 0)
        base_c = np.searchsorted(self.close, s0 * BIG, side="left")
        hi_c = np.searchsorted(self.close, s0 * BIG + t, side="right")
        base_x = np.searchsorted(self.cross, s0 * BIG, side="left")
        lo_x = np.searchsorted(self.cross, s0 * BIG + (t - d_days * DAY_MS), side="right")
        # события, пересечённые не позже t − D, закрыты к t (закрытие ≤ пересечение + 65 мин ≤ t при D ≥ 1 сут.) — вычитаются целиком
        n = (hi_c - base_c) - (lo_x - base_x)
        return np.where(known, n, 0).astype(np.int64)


class History:
    """Причинные счётчики по стороне: `n_ok(m, D)`, `n_fail(m, D)`, `n_stall(m, D)`, `valid(D)` на кандидате (монета, время)."""

    def __init__(self, ev_side, coin_days, names):
        self.names = names
        self.cnt = {}
        for m in MS_NAMES:
            for cls in ("o", "f", "s"):
                k = ev_side.code[m] == cls
                self.cnt[(m, cls)] = Counter(ev_side.sym[k], ev_side.cross[k], ev_side.tres[m][k], names)
        self.coin_days = coin_days            # {монета: булев массив по индексу суток от 2026-06-17} (есть ли файл сделок)

    def reason(self, sym, t_ms, d_days):
        """Почему значение определено / пусто (порядок проверки — по номеру): 0 — определено; 1 — окно `[t − D, t]` начинается до 24.06 (`HIST_START`); 2 — нет суточных файлов монеты;
        3 — **возраст листинга**: окно начинается раньше первых полных суток монеты (у монеты нет полных D суток собственной истории — первые сутки торгов неполные); 4 — в окне пропущены сутки сделок монеты."""
        sym = np.asarray(sym, dtype=object)
        t = np.asarray(t_ms, dtype=np.int64)
        ws = t - d_days * DAY_MS
        out = np.ones(len(t), dtype=np.int8)
        for s in np.unique(sym):
            sel = np.nonzero((sym == s) & (ws >= HIST_START_MS))[0]
            if not len(sel):
                continue
            pres = self.coin_days.get(s)
            if pres is None:
                out[sel] = 2
                continue
            cum = np.concatenate([[0], np.cumsum(pres)])
            d_lo = day_index(ws[sel]) - D17
            d_hi = day_index(t[sel]) - D17
            good = (d_lo >= 0) & (d_hi < len(pres))
            need = np.where(good, d_hi - d_lo + 1, 1)
            have = np.where(good, cum[np.clip(d_hi + 1, 0, len(pres))] - cum[np.clip(d_lo, 0, len(pres))], 0)
            first_full = int(np.argmax(pres)) + 1 if pres.any() else len(pres)         # первые сутки торгов монеты — неполные: окно начинается не раньше следующих суток
            out[sel] = np.where(d_lo < first_full, 3, np.where(good & (have == need), 0, 4))
        return out

    def valid(self, sym, t_ms, d_days):
        """Значение определено: окно целиком в накопленной истории (≥ 24.06), у монеты полные D суток собственной истории после первых неполных суток, без пропущенных суток."""
        return self.reason(sym, t_ms, d_days) == 0

    def counts(self, sym, t_ms, d_days, m):
        return (self.cnt[(m, "o")].count(sym, t_ms, d_days), self.cnt[(m, "f")].count(sym, t_ms, d_days), self.cnt[(m, "s")].count(sym, t_ms, d_days))


D17 = int(datetime.datetime(2026, 6, 17, tzinfo=datetime.timezone.utc).timestamp() * 1000) // DAY_MS


def features(hist, sym, t_ms):
    """{(m, D): (n_ok, n_fail, n_stall, valid)} на кандидатах."""
    out = {}
    for d in DS:
        v = hist.valid(sym, t_ms, d)
        for m in MS_NAMES:
            o, f, s = hist.counts(sym, t_ms, d, m)
            out[(m, d)] = (o, f, s, v)
    return out


def ok_matrix(feats):
    """{col_name: float64 массив, NaN при невалидном окне} — столбцы таблицы `--ftab`."""
    out = {}
    for (m, d), (o, f, s, v) in feats.items():
        out[col_name(m, d)] = np.where(v, o.astype(float), np.nan)
    return out


# ---- таблица --ftab -----------------------------------------------------------------------------------------------------------

def write_ftab(path, sym, sig_ms, cols):
    """CSV `sym,sig_ms,<ok05_d7,…>` (пусто — нет значения); ключ (sym, sig_ms) уникален; порядок — по (sym, sig_ms)."""
    sym = np.asarray(sym, dtype=object)
    order = sorted(range(len(sym)), key=lambda i: (sym[i], int(sig_ms[i])))
    seen = set()
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("sym,sig_ms," + ",".join(COLS) + "\n")
        for i in order:
            key = (sym[i], int(sig_ms[i]))
            if key in seen:
                continue
            seen.add(key)
            f.write("%s,%d,%s\n" % (sym[i], int(sig_ms[i]), ",".join(tf.fmt_val(cols[c][i]) for c in COLS)))
    return len(seen)


def excluded(x, c):
    """Правило фильтра: исключён, если значение определено и x < c (как движок: `fo=lo fv=c`)."""
    with np.errstate(invalid="ignore"):
        return (~np.isnan(x)) & (x < c)


# ---- пороги из распределения (без P&L) ---------------------------------------------------------------------------------------

Q_LEVELS = (0.15, 0.30)


def threshold_for(x, q):
    """Наименьшее целое c ≥ 1, при котором доля сделок со значением и x < c не меньше q (среди сделок со значением). None — значений нет / порог недостижим (max x + 1 всё равно меньше q)."""
    v = x[~np.isnan(x)]
    if len(v) == 0:
        return None
    for c in range(1, int(v.max()) + 2):
        if (v < c).mean() >= q:
            return c
    return None
