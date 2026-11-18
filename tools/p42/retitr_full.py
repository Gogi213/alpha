#!/usr/bin/env python3
"""П-42 §13.24 (TK-599): автомат перетитровки сетапа на 01.07–13.11 — ряды, склейка окон с охраной, Δ $ на сделку, шаг (а)–(з), проходы 1–2,
совместная сетка, проверка процедуры вперёд, собственный вердикт. Движок не запускает: серии берёт у `Evaluator` (кэш по хэшу ключей).

Модель
  Series      — сделки одной конфигурации на всём окне (136 сут. от 2026-07-01): сутки входа, монета, (gross − fees), фандинг движка, граница B.
  splice()    — склейка трёх окон `wa/wb/wc` по зачётным диапазонам; охрана G6 (T0 прогона, диапазоны смыкаются, суммы сходятся, срез daily).
  delta_stat  — Δ = ΣS_c/ΣN_c − ΣS_r/ΣN_r ($ на сделку), круговые блоки по 4 суток, 20 000 повторов, зерно — md5 от рядов; SE, z, нижняя 5 %, ΔΣ, SE_Σ.
  eval_axis   — одна ось при текущей R: победитель = наибольшая Δ > 0, проверяется только он: (а) мин. сделок, (б) z ≥ 2, (в) знак по месяцам,
                (г) плато / z ≥ 2,5, (д) устойчивость, (е) охрана суммы, (ж) фандинг, (з) вето портфеля (сигнальные оси; охрана суммы и на портфельном ряду — §13.24.1).
  run_pass / run_procedure / run_joint — проход, два прохода, совместная сетка; wf_stats — проверка вперёд (расширяющееся окно).
  drive()     — шаговая раздача: процедуры (генераторы) отдают список недостающих прогонов, один общий пакет на раунд, каждая конфигурация считается один раз.
Протокол: docs/research/P-42-level-breakout.md §13.24; сетки — docs/research/freeze/tk599-grids.json; тесты — tools/p42/tests/test_retitr_full.py.
Чтения протокола зарегистрированы в §13.24.1: (б)–(е) — на ряду решения (сигнальный двойник у `sig`-осей, портфель у `port`), (з) — портфельное вето, (е) и на портфельном ряду;
зерно бутстрепа — md5 строки `%.9f` через запятую, массивы S_c|N_c|S_r|N_r через `|`; выбывший по минимуму сосед плато не даёт; проход 2 — база обычная точка, расширение только как сосед.
"""
from __future__ import annotations

import contextlib
import csv
import dataclasses
import datetime
import hashlib
import io
import json
import os
import sys
import zipfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import retitr_cells as rc  # noqa: E402
import retitr_ftab as rfx  # noqa: E402

DAY_MS = 86_400_000
BLOCK = 4
NBOOT = 20_000
D0 = datetime.date(2026, 7, 1)
MONTH_NAMES = ("jul", "aug", "sep", "oct", "nov")
MONTH_LEN = (31, 31, 30, 31, 13)
NDAYS = sum(MONTH_LEN)                                           # 136
MONTH_OF_DAY = np.repeat(np.arange(len(MONTH_LEN)), MONTH_LEN)
MIN_TRADES = (20, 20, 20, 20, 8)                                 # в месяц, у клетки и у эталона; 01–13.11 — 8
SENS_N = (10, 15, 20, 25)                                        # §13.24.5: чувствительность порога; N = 20 — контроль (= основной прогон)
CONTROL_N = 20
Z_STEP, Z_CAT, PLATEAU, VETO_SE = 2.0, 2.5, 0.5, 1.0
GRID_START_DAY, GRID_DAYS, COUNT_DAYS = 14, 80, 60                  # движок: прогрев 14 суток, сетка 80, торговое окно 60 суток (`COUNT_DAYS`, §13.24.3)
TRADE_END = GRID_START_DAY + COUNT_DAYS - 1                       # последние торговые сутки сетки (73); данные окна кончаются T0+73
NOTIONAL = 500.0                                                 # граница фандинга: Σ|ставка|·$500
FOLDS = (((0,), 1), ((0, 1), 2), ((0, 1, 2), 3), ((0, 1, 2, 3), 4))   # (месяцы подбора, месяц проверки)
SIDES = ("L", "S")
ENGINE_SIDE = {"L": "long", "S": "short"}


class GuardError(Exception):
    """Охрана нарезки / сумм / окон сработала — остановка, сообщение координатору."""


# ---- окна ----------------------------------------------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Window:
    name: str
    t0: str      # P42_T0 прогона
    lo: str      # зачётный диапазон по дате входа, включительно
    hi: str

    @property
    def t0d(self):
        return datetime.date.fromisoformat(self.t0)

    @property
    def lod(self):
        return datetime.date.fromisoformat(self.lo)

    @property
    def hid(self):
        return datetime.date.fromisoformat(self.hi)


WINDOWS = (Window("wa", "2026-06-17", "2026-07-01", "2026-08-25"),     # §13.24.4: зачёт 56 + 38 + 42 = 136 суток; глубина предыстории H = 32 у wb, wc (у wa — 14: данных до 17.06 нет)
           Window("wb", "2026-07-25", "2026-08-26", "2026-10-02"),
           Window("wc", "2026-09-01", "2026-10-03", "2026-11-13"))
# G3 (§13.24.4 п. 4): множества сделок соседних окон равны на всём зачётном куске перекрытия — от начала зачёта позднего окна до T0+72 раннего (последние сутки данных — хвост)
G3_RANGES = (("wa", "wb", "2026-08-26", "2026-08-28"), ("wb", "wc", "2026-10-03", "2026-10-05"))
# диагностика разгона (не ворота): та же сверка на [T0 позднего + 14 + 3, начало зачёта − 1] — число различий по суткам
G3_RAMP = (("wa", "wb", "2026-08-11", "2026-08-25"), ("wb", "wc", "2026-09-18", "2026-10-02"))
# проверка сходимости (§13.24.4 п. 5): окно с T0 на 7 суток раньше даёт на зачётном диапазоне тот же набор сделок; сравнение до T0x+69 (хвост 4 сут. — обрезка конца данных)
CONV_WINDOWS = (Window("wbx", "2026-07-18", "2026-08-26", "2026-09-25"),
                Window("wcx", "2026-08-25", "2026-10-03", "2026-11-02"))
CONV_PAIRS = (("wb", "wbx"), ("wc", "wcx"))


def check_windows(windows=WINDOWS, t0_used=None):
    """Окна: T0 прогона = записанному; диапазоны смыкаются без наложений и пропусков, покрывают 01.07–13.11; зачёт внутри торговой части окна (T0+14 … T0+73, §13.24.3)."""
    prev_hi = None
    for w in windows:
        if t0_used is not None and t0_used.get(w.name) != w.t0:
            raise GuardError("окно %s: P42_T0 прогона %r ≠ записанному %s" % (w.name, t0_used.get(w.name), w.t0))
        if w.lod < w.t0d + datetime.timedelta(days=GRID_START_DAY) or w.hid > w.t0d + datetime.timedelta(days=TRADE_END):
            raise GuardError("окно %s: зачётный диапазон %s…%s вне торговой части (T0+%d … T0+%d)" % (w.name, w.lo, w.hi, GRID_START_DAY, TRADE_END))
        if prev_hi is None:
            if w.lod != D0:
                raise GuardError("первое окно начинается %s, а не %s" % (w.lo, D0))
        elif w.lod != prev_hi + datetime.timedelta(days=1):
            raise GuardError("диапазоны не смыкаются: после %s идёт %s" % (prev_hi, w.lo))
        prev_hi = w.hid
    if (prev_hi - D0).days + 1 != NDAYS:
        raise GuardError("диапазоны покрывают %d сут., а нужно %d" % ((prev_hi - D0).days + 1, NDAYS))


SENS_WINDOWS = ("wa", "wb", "wc")                                # §13.24.5 п. 8: `sens` — только эти окна
SENS_LAST_DAY = datetime.date(2026, 11, 13)                      # и ни одной даты позже (17–30.11 не читается)


def check_sens_windows(*window_sets):
    """Изоляция 17–30.11 для `sens` (§13.24.5 п. 8): в каждом наборе окон — ровно wa, wb, wc; зачёт и `T0 + 73` не позже 13.11; иначе GuardError (в т. ч. окно `wd`)."""
    for windows in window_sets:
        names = tuple(w.name for w in windows)
        if names != SENS_WINDOWS:
            raise GuardError("sens: окна %s — разрешены только %s (17–30.11 не читается)" % (list(names), list(SENS_WINDOWS)))
        for w in windows:
            if w.hid > SENS_LAST_DAY or w.t0d + datetime.timedelta(days=TRADE_END) > SENS_LAST_DAY:
                raise GuardError("sens: окно %s (зачёт до %s, данные до %s) выходит за %s" % (w.name, w.hi, w.t0d + datetime.timedelta(days=TRADE_END), SENS_LAST_DAY))


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


# ---- сделки окна, склейка ---------------------------------------------------------------------------------------------------

@dataclasses.dataclass
class TradeTable:
    sym: list
    entry_ms: np.ndarray
    exit_ms: np.ndarray
    gross: np.ndarray
    fees: np.ndarray
    funding: np.ndarray
    net: np.ndarray
    kind: list

    def __len__(self):
        return len(self.sym)


def table_from_rows(rows):
    """rows: список словарей с полями trades.csv (sym, entry_ms, exit_ms, gross, fees, funding, net, kind)."""
    f = lambda k: np.array([float(r[k]) for r in rows], dtype=float)           # noqa: E731
    i = lambda k: np.array([int(r[k]) for r in rows], dtype=np.int64)          # noqa: E731
    return TradeTable([r["sym"] for r in rows], i("entry_ms"), i("exit_ms"), f("gross"), f("fees"), f("funding"), f("net"), [r["kind"] for r in rows])


def read_trades_csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        return table_from_rows(list(csv.DictReader(f)))


def read_daily_csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        return {int(r["day"]): float(r["pnl"]) for r in csv.DictReader(f)}


def abs_day(win, entry_ms):
    """Индекс суток от 2026-07-01 по дате входа: T0 окна + entry_ms // сутки."""
    return (win.t0d - D0).days + np.asarray(entry_ms, dtype=np.int64) // DAY_MS


def _t0_ms(win):
    return int(datetime.datetime(win.t0d.year, win.t0d.month, win.t0d.day, tzinfo=datetime.timezone.utc).timestamp() * 1000)


@dataclasses.dataclass
class Series:
    """Сделки конфигурации на окне 01.07–13.11: все массивы одной длины, порядок — по окнам и времени входа."""
    day: np.ndarray       # индекс суток 0…135
    sym: np.ndarray       # индекс монеты в таблице монет
    val: np.ndarray       # gross − fees (метрика отбора, без фандинга)
    fund: np.ndarray      # фандинг движка (сентябрь–ноябрь; июль–август 0)
    fb: np.ndarray        # граница фандинга B (июль–август; сентябрь–ноябрь 0)
    t_in: np.ndarray = None   # абсолютное время входа, мс эпохи
    t_out: np.ndarray = None

    def __len__(self):
        return len(self.day)


def make_series(day, sym, val, fund=None, fb=None, t_in=None, t_out=None):
    day = np.asarray(day, dtype=np.int64)
    n = len(day)
    z = lambda x: np.zeros(n) if x is None else np.asarray(x, dtype=float)   # noqa: E731
    return Series(day, np.asarray(sym, dtype=np.int64), np.asarray(val, dtype=float), z(fund), z(fb), t_in, t_out)


def funding_bound(sym_names, t_in, t_out, rates, notional=NOTIONAL):
    """B = Σ|ставка|·$500 по выплатам, пересечённым сделкой (t_in < выплата ≤ t_out). rates: {монета: (времена мс по возрастанию, |ставки|)}; нет монеты — 0."""
    out = np.zeros(len(sym_names))
    by = {}
    for i, s in enumerate(sym_names):
        by.setdefault(s, []).append(i)
    for s, ix in by.items():
        if s not in rates:
            continue
        times, ab = rates[s]
        cum = np.concatenate([[0.0], np.cumsum(ab)])
        ix = np.array(ix)
        a = np.searchsorted(times, t_in[ix], side="right")
        b = np.searchsorted(times, t_out[ix], side="right")
        out[ix] = notional * (cum[b] - cum[a])
    return out


def load_funding_rates(root, syms, months=("2026-07", "2026-08", "2026-09")):
    """{монета: (времена, |ставки|)} из `<root>/<SYM>/<SYM>-fundingRate-<YYYY-MM>.zip` (csv calc_time, funding_interval_hours, last_funding_rate); нет файла — пропуск."""
    out = {}
    for s in syms:
        t, r = [], []
        for m in months:
            p = os.path.join(root, s, "%s-fundingRate-%s.zip" % (s, m))
            if not os.path.exists(p):
                continue
            with zipfile.ZipFile(p) as z:
                with z.open(z.namelist()[0]) as f:
                    for row in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8")):
                        t.append(int(row["calc_time"]))
                        r.append(abs(float(row["last_funding_rate"])))
        if t:
            o = np.argsort(t)
            out[s] = (np.array(t, dtype=np.int64)[o], np.array(r)[o])
    return out


def splice(tables, coins, windows=WINDOWS, daily=None, rates=None):
    """Склейка окон → Series. tables: {окно: TradeTable}; daily: {окно: {строка: pnl}} (охрана среза daily); rates — для границы B в июле–августе.
    Охрана G6: net = gross − fees + funding по строкам; Σ(gross − fees) склейки = Σ по зачётным строкам; Σ daily среза = Σ net среза; диапазоны смыкаются."""
    check_windows(windows)
    ci = {c: i for i, c in enumerate(coins)}
    parts, tot_val = [], 0.0
    for w in windows:
        t = tables[w.name]
        d = abs_day(w, t.entry_ms)
        rowtol = 2e-6
        if len(t) and np.abs(t.net - (t.gross - t.fees + t.funding)).max() > rowtol:
            raise GuardError("%s: net ≠ gross − fees + funding в строке сделки (допуск %.0e)" % (w.name, rowtol))
        lo_i, hi_i = (w.lod - D0).days, (w.hid - D0).days
        m = (d >= lo_i) & (d <= hi_i)
        unknown = sorted({t.sym[i] for i in np.nonzero(m)[0] if t.sym[i] not in ci})
        if unknown:
            raise GuardError("%s: монеты вне таблицы: %s" % (w.name, ", ".join(unknown[:5])))
        sel = np.nonzero(m)[0]
        val = (t.gross - t.fees)[sel]
        tot_val += float(val.sum())
        if daily is not None and daily.get(w.name) is not None:
            rows = daily[w.name]
            first = (w.lod - w.t0d).days - GRID_START_DAY         # строка daily = сутки сетки − 14
            last = (w.hid - w.t0d).days - GRID_START_DAY
            ssum = sum(rows.get(i, 0.0) for i in range(first, last + 1))
            nsum = float(t.net[sel].sum())
            tol = max(1e-4, 2e-6 * len(sel))
            if abs(ssum - nsum) > tol:
                raise GuardError("%s: Σ daily среза %+.6f ≠ Σ net сделок среза %+.6f (допуск %.1e)" % (w.name, ssum, nsum, tol))
        t_in = t.entry_ms[sel] + _t0_ms(w)
        t_out = t.exit_ms[sel] + _t0_ms(w)
        parts.append((d[sel], np.array([ci[t.sym[i]] for i in sel], dtype=np.int64), val, t.funding[sel], t_in, t_out, [t.sym[i] for i in sel]))
    day = np.concatenate([p[0] for p in parts]) if parts else np.zeros(0, dtype=np.int64)
    sym = np.concatenate([p[1] for p in parts])
    val = np.concatenate([p[2] for p in parts])
    fund = np.concatenate([p[3] for p in parts])
    t_in = np.concatenate([p[4] for p in parts])
    t_out = np.concatenate([p[5] for p in parts])
    names = [s for p in parts for s in p[6]]
    if abs(float(val.sum()) - tot_val) > 1e-6 * max(1, len(val)):
        raise GuardError("Σ склеенного ряда %+.6f ≠ Σ (gross − fees) зачётных строк %+.6f" % (float(val.sum()), tot_val))
    if len(day) and (day.min() < 0 or day.max() >= NDAYS):
        raise GuardError("сутки вне 0…%d после склейки" % (NDAYS - 1))
    ser = make_series(day, sym, val, fund, None, t_in, t_out)
    if rates is not None:
        fb = funding_bound(names, t_in, t_out, rates)
        fb[MONTH_OF_DAY[ser.day] >= 2] = 0.0                       # сентябрь–ноябрь — фандинг движка, граница не нужна
        ser.fb = fb
    return ser


# ---- ворота окон ----------------------------------------------------------------------------------------------------------

def _keyset(t, w, lo, hi):
    """{(монета, абсолютный вход, абсолютный выход): (gross, fees)} по сделкам окна с датой входа lo…hi включительно."""
    d = abs_day(w, t.entry_ms)
    a, b = (datetime.date.fromisoformat(lo) - D0).days, (datetime.date.fromisoformat(hi) - D0).days
    out = {}
    for i in np.nonzero((d >= a) & (d <= b))[0]:
        out[(t.sym[i], int(t.entry_ms[i]) + _t0_ms(w), int(t.exit_ms[i]) + _t0_ms(w))] = (round(float(t.gross[i]), 6), round(float(t.fees[i]), 6))
    return out


def overlap_diff(ta, wa, tb, wb, lo, hi):
    """G3 / проверка сходимости: на куске lo…hi (даты входа, включительно) множества сделок двух окон равны по (монета, абсолютный вход, выход, gross, fees). Возвращает список различий."""
    A, B = _keyset(ta, wa, lo, hi), _keyset(tb, wb, lo, hi)
    diff = ["только в %s: %s" % (wa.name, k) for k in sorted(set(A) - set(B))] + ["только в %s: %s" % (wb.name, k) for k in sorted(set(B) - set(A))]
    for k in sorted(set(A) & set(B)):
        if abs(A[k][0] - B[k][0]) > 1e-6 or abs(A[k][1] - B[k][1]) > 1e-6:
            diff.append("различие gross/fees %s: %s ≠ %s" % (k, A[k], B[k]))
    return diff


def diff_by_day(ta, wa, tb, wb, lo, hi):
    """Диагностика разгона (не ворота): {дата входа: число различий} на куске lo…hi — только сутки с различиями; число суток и сделок в куске — `n_days`, `n_trades` (по окну wa)."""
    A, B = _keyset(ta, wa, lo, hi), _keyset(tb, wb, lo, hi)
    day = lambda k: datetime.datetime.fromtimestamp(k[1] / 1000, tz=datetime.timezone.utc).date().isoformat()     # noqa: E731
    bad = list(set(A) ^ set(B)) + [k for k in set(A) & set(B) if abs(A[k][0] - B[k][0]) > 1e-6 or abs(A[k][1] - B[k][1]) > 1e-6]
    out = {}
    for k in bad:
        out[day(k)] = out.get(day(k), 0) + 1
    return {"by_day": dict(sorted(out.items())), "n_trades": len(A), "n_diff": len(bad)}


def forced_in_range(t, w):
    """G3b: сделки с kind=forced (закрыты обрезкой конца данных) среди зачётных строк окна: [(монета, дата входа)]. Допустимы только у wc на последних сутках (13.11)."""
    d = abs_day(w, t.entry_ms)
    lo_i, hi_i = (w.lod - D0).days, (w.hid - D0).days
    return [(t.sym[i], str(D0 + datetime.timedelta(days=int(d[i])))) for i in np.nonzero((d >= lo_i) & (d <= hi_i))[0] if t.kind[i] == "forced"]


def check_forced(tables, windows=WINDOWS):
    bad, ok = [], 0
    for w in windows:
        for sym, date in forced_in_range(tables[w.name], w):
            if w.name == "wc" and date == w.hi:
                ok += 1
            else:
                bad.append((w.name, sym, date))
    if bad:
        raise GuardError("G3b: сделки, закрытые обрезкой конца данных, в зачётных диапазонах: %s" % bad[:5])
    return ok


DATA_END = datetime.date(2026, 11, 13)                            # последние сутки данных (сутки 135 склейки)


def check_data_end(tables, windows=WINDOWS):
    """§13.24.3 п. 7 (критик): на пустых хвостах данных (wc: суток сетки 54–73) нет сделок — вход и выход каждой сделки не позже конца данных окна
    (min(T0+73, 13.11), включительно). Возвращает {окно: последний выход (мс от T0)}; нарушение — GuardError."""
    last = {}
    for w in windows:
        t = tables[w.name]
        end_d = min(w.t0d + datetime.timedelta(days=TRADE_END), DATA_END)
        lim = ((end_d - w.t0d).days + 1) * DAY_MS
        last[w.name] = int(t.exit_ms.max()) if len(t.exit_ms) else 0
        if len(t.exit_ms) and (int(t.exit_ms.max()) > lim or int(t.entry_ms.max()) >= lim):
            raise GuardError("окно %s: сделки позже конца данных %s (последний выход +%d мс от T0, предел %d)" % (w.name, end_d, int(t.exit_ms.max()), lim))
    return last


def feature_availability(values, months, min_share=0.95):
    """G5: признак титруется, если доля непустых значений у кандидатов в КАЖДОМ из пяти месяцев ≥ 95 %. values — массив (NaN = пусто); months — индекс месяца кандидата."""
    v, m = np.asarray(values, dtype=float), np.asarray(months)
    shares = []
    for k in range(len(MONTH_LEN)):
        s = m == k
        shares.append(float((~np.isnan(v[s])).mean()) if s.any() else 0.0)
    return all(x >= min_share for x in shares), shares


def feature_cuts(values, days, train_days, fo, pcts=(10, 20, 25)):
    """fv срезов по кандидатам ОБУЧАЮЩИХ суток (NaN не считаются): lo — q(p), hi — q(1 − p), numpy.quantile (линейная) — как retitr_ftab.quantile_cut."""
    v, d = np.asarray(values, dtype=float), np.asarray(days)
    m = np.isin(d, np.asarray(train_days))
    return {int(p): rfx.quantile_cut(v[m], fo, p) for p in pcts}


# ---- Δ $ на сделку, бутстреп -----------------------------------------------------------------------------------------------

def day_sums(ser, vals=None):
    v = ser.val if vals is None else vals
    return np.bincount(ser.day, weights=v, minlength=NDAYS), np.bincount(ser.day, minlength=NDAYS).astype(float)


def _fmt9(a):
    return ",".join("%.9f" % (round(float(x), 9) + 0.0) for x in a)


def seed_of(Sc, Nc, Sr, Nr):
    return int(hashlib.md5("|".join(_fmt9(a) for a in (Sc, Nc, Sr, Nr)).encode()).hexdigest()[:8], 16)


def block_idx(n, rng, nboot=NBOOT, block=BLOCK):
    nb = -(-n // block)
    starts = rng.integers(0, n, size=(nboot, nb))
    return ((starts[:, :, None] + np.arange(block)[None, None, :]) % n).reshape(nboot, nb * block)[:, :n]


def ratio(s, n):
    return float(s) / float(n) if n > 0 else float("nan")


@dataclasses.dataclass
class DStat:
    d: float
    se: float
    z: float
    lower: float
    dsum: float
    se_sum: float
    s_c: float
    s_r: float
    n_c: int
    n_r: int
    n_days: int


def _z(d, se):
    if se > 0:
        return d / se
    return float("inf") if d > 0 else (float("-inf") if d < 0 else 0.0)


def delta_stat(Sc, Nc, Sr, Nr, nboot=NBOOT):
    """Парная Δ среднего $ на сделку клетка − эталон по суткам Ω (одни и те же блоки суток); нижняя граница — 5-й перцентиль; ΔΣ и SE_Σ — по тем же выборкам."""
    Sc, Nc, Sr, Nr = (np.asarray(a, dtype=float) for a in (Sc, Nc, Sr, Nr))
    n = len(Sc)
    if Nc.sum() <= 0 or Nr.sum() <= 0:
        raise GuardError("Δ не определена: у клетки или эталона нет сделок в Ω")
    d = ratio(Sc.sum(), Nc.sum()) - ratio(Sr.sum(), Nr.sum())
    idx = block_idx(n, np.random.default_rng(seed_of(Sc, Nc, Sr, Nr)), nboot)
    bSc, bNc, bSr, bNr = (a[idx].sum(axis=1) for a in (Sc, Nc, Sr, Nr))
    ok = (bNc > 0) & (bNr > 0)
    if ok.mean() < 0.99:
        raise GuardError("бутстреп: у %.1f %% повторов нет сделок у клетки или эталона" % (100 * (1 - ok.mean())))
    bd = bSc[ok] / bNc[ok] - bSr[ok] / bNr[ok]
    se = float(bd.std(ddof=1))
    bs = (Sc - Sr)[idx].sum(axis=1)
    return DStat(d, se, _z(d, se), float(np.percentile(bd, 5)), float(Sc.sum() - Sr.sum()), float(bs.std(ddof=1)),
                 float(Sc.sum()), float(Sr.sum()), int(Nc.sum()), int(Nr.sum()), n)


@dataclasses.dataclass
class OwnStat:
    mean: float
    se: float
    lower: float
    upper: float
    p: float
    total: float
    n: int


def own_stat(S, N, nboot=NBOOT):
    """Среднее $ на сделку ряда (суточные S, N), блочный бутстреп по суткам; односторонний p (H0: среднее ≤ 0) = (1 + #(бутстреп ≤ 0)) / (1 + повторов)."""
    S, N = np.asarray(S, dtype=float), np.asarray(N, dtype=float)
    if N.sum() <= 0:
        raise GuardError("нет сделок")
    idx = block_idx(len(S), np.random.default_rng(int(hashlib.md5(("|".join(_fmt9(a) for a in (S, N))).encode()).hexdigest()[:8], 16)), nboot)
    bS, bN = S[idx].sum(axis=1), N[idx].sum(axis=1)
    ok = bN > 0
    b = bS[ok] / bN[ok]
    p = (1 + int((b <= 0).sum())) / (1 + len(b))
    return OwnStat(ratio(S.sum(), N.sum()), float(b.std(ddof=1)), float(np.percentile(b, 5)), float(np.percentile(b, 95)), float(p), float(S.sum()), int(N.sum()))


def holm_reject(ps, alpha=0.05):
    m = len(ps)
    rej = [False] * m
    for k, i in enumerate(sorted(range(m), key=lambda j: ps[j]), start=1):
        if ps[i] <= alpha / (m - k + 1):
            rej[i] = True
        else:
            break
    return rej


# ---- месяцы, минимум сделок, знак по месяцам, устойчивость ---------------------------------------------------------------------

def month_days(months):
    return np.nonzero(np.isin(MONTH_OF_DAY, list(months)))[0]


def need_months(n):
    return {1: 0, 2: 2, 3: 2, 4: 3, 5: 4}[n]


def month_table(Sc, Nc, Sr, Nr, om):
    """По месяцам Ω: {месяц: (Δ $/сделку, N клетки, N эталона)}; Sc… — суточные массивы длины NDAYS."""
    out = {}
    mo = MONTH_OF_DAY[om]
    for k in sorted(set(mo.tolist())):
        d = om[mo == k]
        nc, nr = Nc[d].sum(), Nr[d].sum()
        out[k] = (ratio(Sc[d].sum(), nc) - ratio(Sr[d].sum(), nr) if nc > 0 and nr > 0 else float("nan"), int(nc), int(nr))
    return out


def min_trades_ok(mt):
    return all(nc >= MIN_TRADES[k] and nr >= MIN_TRADES[k] for k, (_, nc, nr) in mt.items())


def min_trades_for(n):
    """Порог месяца при N (§13.24.5): N сделок в месяц у клетки и у эталона; у 01–13.11 (13 суток) — 0,4·N (8 при N = 20)."""
    if int(n) != n or n <= 0:
        raise GuardError("порог минимума сделок N = %r: нужно целое > 0" % (n,))
    return (int(n),) * (len(MONTH_LEN) - 1) + (int(round(0.4 * n)),)


@contextlib.contextmanager
def min_trades_override(n):
    """На время блока `MIN_TRADES` = порог при N (§13.24.5: чувствительность); возврат — в `finally`. Не потокобезопасно (процедура однопоточна)."""
    global MIN_TRADES
    old = MIN_TRADES
    MIN_TRADES = min_trades_for(n)
    try:
        yield MIN_TRADES
    finally:
        MIN_TRADES = old


def sign_ok(mt):
    return sum(1 for (d, _, _) in mt.values() if d > 0) >= need_months(len(mt))


def _greedy(Sc, Nc, Sr, Nr, k=2):
    """Жадное удаление k единиц (суток или монет; у клетки и эталона одновременно): каждый раз той, что снимает Δ сильнее всего. Δ после удаления; None — единиц < k + 1."""
    act = np.nonzero((Nc + Nr) > 0)[0]
    if len(act) <= k:
        return None
    Sc, Nc, Sr, Nr = Sc[act].copy(), Nc[act].copy(), Sr[act].copy(), Nr[act].copy()
    alive = np.ones(len(act), dtype=bool)
    for _ in range(k):
        tc, tn, rc_, rn = Sc[alive].sum(), Nc[alive].sum(), Sr[alive].sum(), Nr[alive].sum()
        best, bi = None, None
        for i in np.nonzero(alive)[0]:
            if tn - Nc[i] <= 0 or rn - Nr[i] <= 0:
                continue
            v = (tc - Sc[i]) / (tn - Nc[i]) - (rc_ - Sr[i]) / (rn - Nr[i])
            if best is None or v < best - 1e-15:
                best, bi = v, i
        if bi is None:
            return None
        alive[bi] = False
    tc, tn, rc_, rn = Sc[alive].sum(), Nc[alive].sum(), Sr[alive].sum(), Nr[alive].sum()
    return ratio(tc, tn) - ratio(rc_, rn) if tn > 0 and rn > 0 else None


def robust(c, r, om, ncoins):
    """Устойчивость: Δ > 0 после жадного удаления двух суток и (отдельно) двух монет. Возвращает (ок, Δ без суток, Δ без монет)."""
    Sc, Nc = day_sums(c)
    Sr, Nr = day_sums(r)
    dd = _greedy(Sc[om], Nc[om], Sr[om], Nr[om])
    inom = np.zeros(NDAYS, dtype=bool)
    inom[om] = True

    def coin(ser):
        m = inom[ser.day]
        return np.bincount(ser.sym[m], weights=ser.val[m], minlength=ncoins), np.bincount(ser.sym[m], minlength=ncoins).astype(float)
    (a, b), (e, f) = coin(c), coin(r)
    dc = _greedy(a, b, e, f)
    return bool(dd is not None and dc is not None and dd > 0 and dc > 0), dd, dc


def sens_arrays(c, r, om):
    """Пессимистичная чувствительность к фандингу: у клетки val + fund − B, у эталона val + fund + B (июль–август B из ставок, сентябрь–ноябрь fund движка)."""
    Sc, Nc = day_sums(c, c.val + c.fund - c.fb)
    Sr, Nr = day_sums(r, r.val + r.fund + r.fb)
    return Sc, Nc, Sr, Nr


# ---- ключи, конфигурация, оси -------------------------------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Req:
    side: str
    keys: str
    sig: bool

    @property
    def hash(self):
        return hashlib.sha256(repr((self.side, rc.canon(self.keys), bool(self.sig))).encode()).hexdigest()

    @property
    def engine_keys(self):
        return rc.merge_keys(self.keys, rc.SIGMODE) if self.sig else self.keys

    @property
    def vid(self):
        return "c" + self.hash[:12]


class Spec:
    """Сетки из `tk599-grids.json` (заморожены): базы, значения баз, оси по сторонам (порядок групп и осей = порядок в файле)."""

    def __init__(self, d):
        self.d = d
        self.bases = d["bases"]
        self.base_values = d["base_values"]
        self.axes = d["sides"]
        self.index = {s: {a["axis"]: i for i, a in enumerate(d["sides"][s])} for s in d["sides"]}

    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f))

    def features(self, side):
        return [(x["fa"], x["fo"]) for x in self.d["features"][side]]


def patch_str(patch):
    return " ".join("%s=%s" % kv for kv in patch.items())


def cfg_keys(spec, side, cfg):
    """База ⊕ патчи принятых клеток в порядке осей (позже — главнее; признаки F — после обычных осей, в порядке принятия)."""
    names = sorted(cfg, key=lambda n: spec.index[side].get(n, 10 ** 6))
    keys = rc.merge_keys(spec.bases[side], *[patch_str(cfg[n]["patch"]) for n in names])
    if len(rc.parse_keys(keys)[1]) > 1:     # движок v21: ОДИН фильтр признака на вариант (params.rs `FilterCfg`), повторные fa/fo/fv молча перезаписывают — запрещено
        raise GuardError("в конфигурации больше одного фильтра признака (fa/fo/fv): %s" % keys)
    return keys


def feature_axes(side, cuts):
    """Группа 6 — ОДНА категориальная ось `F`: клетки = (признак, направление) × срез 10 / 20 / 25 %; cuts = {(fa, fo): {pct: fv}}. Движок v21 держит один фильтр признака
    на вариант (`FilterCfg`: повторные fa/fo/fv перезаписывают молча), поэтому в конфигурации не больше одного признака; принятый признак заменяется другой клеткой оси."""
    cells = [{"id": "F_%s_%s_%d" % (fa, fo, p), "patch": {"fa": fa, "fo": fo, "fv": repr(float(fv))}} for (fa, fo), c in cuts.items() for p, fv in sorted(c.items())]
    return [{"axis": "F", "group": 6, "mode": "port", "class": "categorical", "cells": cells, "order": [], "ext": {}}] if cells else []


@dataclasses.dataclass
class Point:
    cell: object      # {id, patch} или None — база оси
    value: float      # для упорядоченных
    ext: bool
    current: bool

    @property
    def cid(self):
        return "base" if self.cell is None else self.cell["id"]


def axis_points(spec, side, axis, cfg):
    name = axis["axis"]
    cur = cfg.get(name)
    ordered = axis["class"] == "ordered"
    val = (lambda c: float(c["patch"][name])) if ordered else (lambda c: 0.0)   # noqa: E731
    pts = [Point(c, val(c), False, cur is not None and cur["id"] == c["id"]) for c in axis["cells"]]
    pts.append(Point(None, float(spec.base_values[side][name]) if ordered else 0.0, False, cur is None))
    for k in ("low", "high"):
        if axis["ext"].get(k):
            pts.append(Point(axis["ext"][k], val(axis["ext"][k]), True, False))
    if ordered:
        pts.sort(key=lambda p: p.value)
    return pts


def neighbours(pts, p):
    """Ближайшие точки слева и справа (текущее значение пропускается; расширение — обычная точка)."""
    i = pts.index(p)
    left = next((q for q in reversed(pts[:i]) if not q.current), None)
    right = next((q for q in pts[i + 1:] if not q.current), None)
    return left, right


def with_cell(cfg, axis, cell):
    out = dict(cfg)
    if cell is None:
        out.pop(axis, None)
    else:
        out[axis] = cell
    return out


# ---- вычислитель: кэш по хэшу ключей, шаговая раздача ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class FtabNeed:
    """Нужна таблица признаков кандидатов потока конфигурации `keys` (группа 6): строится отдельно от прогонов портфеля."""
    side: str
    keys: str

    @property
    def hash(self):
        return "ftab-" + hashlib.sha256(repr((self.side, rc.canon(self.keys))).encode()).hexdigest()

    @property
    def vid(self):
        return "f" + self.hash[5:17]


class Evaluator:
    """Кэш серий по хэшу (сторона, эффективные ключи, сигнальный ли двойник). `fn(req) -> Series` — как посчитать (движок на srv / синтетика)."""

    def __init__(self, fn=None):
        self.fn = fn
        self.cache = {}
        self.batches = []

    def missing(self, reqs):
        seen, out = set(), []
        for r in reqs:
            if r.hash not in self.cache and r.hash not in seen:
                seen.add(r.hash)
                out.append(r)
        return out

    def fulfil(self, reqs):
        reqs = self.missing(reqs)
        self.batches.append(len(reqs))
        for r in reqs:
            self.cache[r.hash] = self.fn(r)

    def get(self, req):
        try:
            return self.cache[req.hash]
        except KeyError:
            raise GuardError("прогон %s (%s) не посчитан — нужен батч до чтения" % (req.vid, req.engine_keys)) from None


def _need(ev, reqs):
    miss = ev.missing(reqs)
    if miss:
        yield miss


def drive(gens, ev):
    """Раздача: каждый генератор отдаёт список недостающих прогонов или заканчивается (значение — результат). За раунд — один общий пакет без повторов."""
    live, res = dict(gens), {}
    while live:
        batch = []
        for name in list(live):
            try:
                batch += next(live[name])
            except StopIteration as e:
                res[name] = e.value
                del live[name]
        if batch:
            ev.fulfil(batch)
    return res


class OutDirEvaluator(Evaluator):
    """Серии из выходов движка: `<outroot>/<окно>/<vid>/{long,short}-trades.csv` (+ `-daily.csv`); склейка с охраной G6, кэш .npz на диске. `launch(reqs)` — запуск движка на srv (задаёт драйвер)."""

    def __init__(self, outroot, coins, rates, launch=None, cache_dir=None, windows=WINDOWS, run_t0=None, ftab_builder=None, ftab_ready=None,
                 bin_md5=None, allowed_md5=None, legacy_md5=None):
        super().__init__(None)
        self.bin_md5, self.allowed_md5, self.legacy_md5 = bin_md5, allowed_md5, legacy_md5   # §13.24.6: серия несёт md5 бинаря; на загрузке — только допустимые (без поля — старый бинарь)
        self.outroot, self.coins, self.rates, self.launch, self.cache_dir = outroot, coins, rates, launch, cache_dir
        self.ftab_builder, self.ftab_ready = ftab_builder, ftab_ready
        self.windows, self.run_t0 = windows, run_t0
        if rates is None:
            raise GuardError("OutDirEvaluator: нужны ставки фандинга (граница B июль–август)")

    def variants_text(self, reqs):
        return "".join("%s %s\n" % (r.vid, r.engine_keys) for r in reqs)

    def ingest(self, req):
        tables, daily = {}, {}
        for w in self.windows:
            d = os.path.join(self.outroot, w.name, req.vid)
            sd = ENGINE_SIDE[req.side]
            tables[w.name] = read_trades_csv(os.path.join(d, sd + "-trades.csv"))
            if "fa=" in req.keys:
                self._check_filter_rows(os.path.join(d, "meta.json"), sd, req)
            dp = os.path.join(d, sd + "-daily.csv")
            daily[w.name] = read_daily_csv(dp) if os.path.exists(dp) else None
        check_windows(self.windows, self.run_t0)
        return splice(tables, self.coins, self.windows, daily, self.rates)

    @staticmethod
    def _check_filter_rows(path, side_name, req):
        """Фильтр признака: кандидаты без строки таблицы проходят молча — требуем no_key = 0 (таблица обязана покрывать поток варианта)."""
        with open(path, encoding="utf-8") as f:
            st = json.load(f)[side_name].get("filter_[cands,dropped,no_key,no_value]")
        if st is None:
            raise GuardError("%s: в meta.json нет счётчиков фильтра признака — фильтр не применялся" % req.vid)
        if st[2] != 0:
            raise GuardError("%s: у %d из %d кандидатов нет строки таблицы признаков (no_key) — таблица не покрывает поток варианта" % (req.vid, st[2], st[0]))

    def _npz(self, req):
        return os.path.join(self.cache_dir, req.hash + ".npz") if self.cache_dir else None

    def missing(self, reqs):
        miss = []
        for r in super().missing(reqs):
            if isinstance(r, FtabNeed):
                if self.ftab_ready is not None and self.ftab_ready(r):
                    self.cache[r.hash] = True
                else:
                    miss.append(r)
                continue
            p = self._npz(r)
            if p and os.path.exists(p):
                z = np.load(p)
                if self.allowed_md5 is not None:
                    got = str(z["bin_md5"]) if "bin_md5" in z.files else self.legacy_md5
                    if got not in self.allowed_md5:
                        raise GuardError("%s: серия посчитана бинарём md5 %s — не из допустимых %s" % (p, got, sorted(self.allowed_md5)))
                self.cache[r.hash] = Series(z["day"], z["sym"], z["val"], z["fund"], z["fb"], z["t_in"], z["t_out"])
            else:
                miss.append(r)
        return miss

    def fulfil(self, reqs):
        reqs = self.missing(reqs)
        if not reqs:
            return
        self.batches.append(len(reqs))
        needs = [r for r in reqs if isinstance(r, FtabNeed)]
        reqs = [r for r in reqs if not isinstance(r, FtabNeed)]
        if needs:
            if self.ftab_builder is None:
                raise GuardError("нет сборщика таблиц признаков (ftab_builder) для %d потоков" % len(needs))
            self.ftab_builder(needs)
            for n in needs:
                self.cache[n.hash] = True
        if not reqs:
            return
        if self.launch is None:
            raise GuardError("нет запуска движка (launch) для %d прогонов" % len(reqs))
        self.launch(reqs)
        for r in reqs:
            s = self.ingest(r)
            self.cache[r.hash] = s
            p = self._npz(r)
            if p:
                extra = {"bin_md5": np.array(self.bin_md5)} if self.bin_md5 else {}
                np.savez(p, day=s.day, sym=s.sym, val=s.val, fund=s.fund, fb=s.fb, t_in=s.t_in, t_out=s.t_out, **extra)


# ---- шаг по оси ---------------------------------------------------------------------------------------------------------------

@dataclasses.dataclass
class Step:
    axis: str
    group: int
    mode: str
    cls: str
    cur: object = None
    winner: object = None
    cell: object = None            # патч-клетка победителя (None у «base» и когда победителя нет)
    accepted: bool = False
    reasons: list = dataclasses.field(default_factory=list)
    rows: list = dataclasses.field(default_factory=list)       # [{id, d, n_c, n_r, kept}]
    cells: dict = dataclasses.field(default_factory=dict)      # id → клетка
    n_cells: int = 0
    stat: object = None
    detail: dict = dataclasses.field(default_factory=dict)


def _row_stats(c, r, om):
    Sc, Nc = day_sums(c)
    Sr, Nr = day_sums(r)
    mt = month_table(Sc, Nc, Sr, Nr, om)
    nc, nr = Nc[om].sum(), Nr[om].sum()
    d = ratio(Sc[om].sum(), nc) - ratio(Sr[om].sum(), nr) if nc > 0 and nr > 0 else float("nan")
    return d, mt, (Sc, Nc, Sr, Nr)


def conditions(c, r, om, ncoins, nboot=NBOOT):
    """Условия (а), (б), (в), (д), (е), (ж) для пары (клетка, эталон) на Ω; (г) плато / z ≥ 2,5 и (з) вето — по контексту. Возвращает (stat, {условие: (ок, пояснение)})."""
    Sc, Nc = day_sums(c)
    Sr, Nr = day_sums(r)
    st = delta_stat(Sc[om], Nc[om], Sr[om], Nr[om], nboot)
    mt = month_table(Sc, Nc, Sr, Nr, om)
    res = {"a_min_trades": (min_trades_ok(mt), {k: v[1:] for k, v in mt.items()}),
           "b_z": (st.z >= Z_STEP, "z %.2f" % st.z),
           "c_months": (sign_ok(mt), {MONTH_NAMES[k]: round(v[0], 4) for k, v in mt.items()}),
           "d_robust": None, "e_sum": (st.s_c > 0 and st.dsum >= -1.0 * st.se_sum, "ΣS %+.2f, ΔΣ %+.2f, SE_Σ %.2f" % (st.s_c, st.dsum, st.se_sum))}
    ok, dd, dc = robust(c, r, om, ncoins)
    res["d_robust"] = (ok, "без 2 суток %s, без 2 монет %s" % (dd, dc))
    fSc, fNc, fSr, fNr = sens_arrays(c, r, om)
    fs = delta_stat(fSc[om], fNc[om], fSr[om], fNr[om], nboot)
    fmt = month_table(fSc, fNc, fSr, fNr, om)
    res["f_funding"] = (fs.z >= Z_STEP and sign_ok(fmt), "Δ' %+.3f z' %.2f" % (fs.d, fs.z))
    return st, res


def eval_axis(ev, spec, side, axis, cfg, om, ncoins, nboot=NBOOT):
    """Шаг по оси при текущей конфигурации cfg на Ω (дни om). Генератор: отдаёт недостающие прогоны, возвращает Step."""
    name = axis["axis"]
    sig = axis["mode"] == "sig"
    st = Step(name, axis["group"], axis["mode"], axis["class"], cur=(cfg[name]["id"] if name in cfg else None))
    ref_keys = cfg_keys(spec, side, cfg)
    pts = axis_points(spec, side, axis, cfg)
    ref_canon = rc.canon(ref_keys)
    cands = []
    for p in pts:
        if p.ext or p.current:
            continue
        keys = cfg_keys(spec, side, with_cell(cfg, name, p.cell))
        if rc.canon(keys) == ref_canon:
            continue                                       # клетка, равная эталону по эффективным ключам, не клетка
        cands.append((p, keys))
    if not cands:
        st.reasons.append("нет клеток, отличных от текущей")
        return st
    yield from _need(ev, [Req(side, ref_keys, sig)] + [Req(side, k, sig) for _, k in cands])
    ref = ev.get(Req(side, ref_keys, sig))
    rows = {}
    for p, keys in cands:
        c = ev.get(Req(side, keys, sig))
        d, mt, _ = _row_stats(c, ref, om)
        kept = (d == d) and min_trades_ok(mt)
        rows[p.cid] = dict(id=p.cid, d=d, n_c=sum(v[1] for v in mt.values()), n_r=sum(v[2] for v in mt.values()), kept=bool(kept), keys=keys, point=p)
        st.cells[p.cid] = p.cell
    st.n_cells = len(cands)
    st.rows = [{k: v for k, v in r.items() if k not in ("point", "keys")} for r in rows.values()]
    pool = [r for r in rows.values() if r["kept"]]
    if not pool:
        st.reasons.append("(а) ни у одной клетки нет минимума сделок по месяцам")
        return st
    win = max(pool, key=lambda r: (round(r["d"], 9), -list(rows).index(r["id"])))
    st.winner, st.cell = win["id"], win["point"].cell
    if not win["d"] > 0:
        st.reasons.append("победителя нет: наибольшая Δ %+.4f ≤ 0 (%s)" % (win["d"], win["id"]))
        return st
    wc = ev.get(Req(side, win["keys"], sig))
    stat, cond = conditions(wc, ref, om, ncoins, nboot)
    st.stat = stat
    reasons = {k: v[1] for k, v in cond.items() if k[0] != "_"}
    # (г) плато / категориальный порог
    if axis["class"] == "ordered":
        left, right = neighbours(pts, win["point"])
        nbs = [q for q in (left, right) if q is not None]
        ext_req = [Req(side, cfg_keys(spec, side, with_cell(cfg, name, q.cell)), sig) for q in nbs if q.ext]
        if ext_req:
            yield from _need(ev, ext_req)
        det, plat_ok = {}, len(nbs) == 2
        if not plat_ok:
            reasons["g_plateau"] = "нет двух соседей (клетка на краю без расширения)"
        for q in nbs:
            if q.ext:
                kq = cfg_keys(spec, side, with_cell(cfg, name, q.cell))
                st.n_cells += 1
                d_q, mt_q, _ = _row_stats(ev.get(Req(side, kq, sig)), ref, om)
                if d_q > win["d"]:
                    plat_ok = False
                    reasons["g_ext"] = "расширение %s лучше края: Δ %+.4f > %+.4f — ось не меняется" % (q.cid, d_q, win["d"])
                kept_q = (d_q == d_q) and min_trades_ok(mt_q)
            else:
                rq = rows.get(q.cid)
                d_q, kept_q = (rq["d"], rq["kept"]) if rq else (float("nan"), False)
            det[q.cid] = d_q
            if not kept_q or not d_q >= PLATEAU * win["d"]:
                plat_ok = False
        reasons.setdefault("g_plateau", "соседи %s: порог %.4f" % ({k: round(v, 4) for k, v in det.items()}, PLATEAU * win["d"]))
        cond["g_plateau"] = (plat_ok, ((reasons["g_ext"] + "; ") if "g_ext" in reasons else "") + reasons["g_plateau"])
    else:
        cond["g_cat"] = (stat.z >= Z_CAT, "z %.2f ≥ %.1f" % (stat.z, Z_CAT))
    # (з) вето портфеля (сигнальные оси)
    if sig:
        yield from _need(ev, [Req(side, win["keys"], False), Req(side, ref_keys, False)])
        pc, pr = ev.get(Req(side, win["keys"], False)), ev.get(Req(side, ref_keys, False))
        pSc, pNc = day_sums(pc)
        pSr, pNr = day_sums(pr)
        ps = delta_stat(pSc[om], pNc[om], pSr[om], pNr[om], nboot)
        cond["h_veto"] = (ps.d >= -VETO_SE * ps.se, "портфельная Δ %+.4f, SE %.4f" % (ps.d, ps.se))
        cond["e_sum_port"] = (ps.s_c > 0 and ps.dsum >= -1.0 * ps.se_sum, "портфель: ΣS %+.2f, ΔΣ %+.2f, SE_Σ %.2f" % (ps.s_c, ps.dsum, ps.se_sum))   # §13.24.1 п. 1
    fails = [k for k, v in cond.items() if k[0] != "_" and not v[0]]
    st.detail = {k: v for k, v in cond.items() if k[0] != "_"}
    st.reasons += ["%s: %s" % (k, cond[k][1]) for k in fails]
    st.accepted = not fails
    return st


def apply_step(cfg, step):
    return with_cell(cfg, step.axis, step.cell) if step.accepted else cfg


def run_pass(ev, spec, side, cfg, om, ncoins, provider=None, nboot=NBOOT):
    """Проход по группам 1–5 (осям спека), затем группа 6 (признаки; границы — у `provider`). Принятое сразу входит в R. Генератор → (cfg, шаги)."""
    steps, cfg = [], dict(cfg)
    for axis in spec.axes[side]:
        s = yield from eval_axis(ev, spec, side, axis, cfg, om, ncoins, nboot)
        steps.append(s)
        cfg = apply_step(cfg, s)
    if provider is not None:
        cuts = yield from provider(side, cfg_keys(spec, side, {k: v for k, v in cfg.items() if k != "F"}), om)   # поток после группы 5 — без фильтра признака
        for axis in feature_axes(side, cuts):
            s = yield from eval_axis(ev, spec, side, axis, cfg, om, ncoins, nboot)
            steps.append(s)
            cfg = apply_step(cfg, s)
    return cfg, steps


def top_two(step):
    """Два лучших значения оси по Δ прохода 1 (только Δ > 0; второго нет — одно): клетки."""
    rs = sorted([r for r in step.rows if r["kept"] and r["d"] > 0], key=lambda r: (-round(r["d"], 9)))
    return [step.cells[r["id"]] for r in rs[:2] if step.cells.get(r["id"]) is not None]


@dataclasses.dataclass
class Joint:
    axes: list
    cells: list = dataclasses.field(default_factory=list)      # [{cfg, d, passed, fails}]
    chosen: object = None
    n_cells: int = 0


def run_joint(ev, spec, side, cfg1, steps1, om, ncoins, nboot=NBOOT):
    """Совместная сетка: по принятым осям два лучших значения прохода 1, 2^k клеток, портфель, против БАЗЫ стороны; (а)–(ж) в категориальной форме (z ≥ 2,5, без плато)."""
    acc = [s for s in steps1 if s.accepted and s.axis in cfg1]
    jt = Joint([s.axis for s in acc])
    if not acc:
        return jt
    combos = [{}]
    for s in acc:
        vals = top_two(s) or [cfg1[s.axis]]
        combos = [dict(c, **{s.axis: v}) for c in combos for v in vals]
    base_keys = cfg_keys(spec, side, {})
    reqs = [Req(side, base_keys, False)] + [Req(side, cfg_keys(spec, side, c), False) for c in combos]
    yield from _need(ev, reqs)
    ref = ev.get(Req(side, base_keys, False))
    rows = []
    for c in combos:
        keys = cfg_keys(spec, side, c)
        if rc.canon(keys) == rc.canon(base_keys):
            continue
        ser = ev.get(Req(side, keys, False))
        d, mt, _ = _row_stats(ser, ref, om)
        rows.append(dict(cfg=c, keys=keys, d=d, mt=mt))
    jt.n_cells = len(rows)
    for r in sorted(rows, key=lambda r: -round(r["d"], 9) if r["d"] == r["d"] else 1e9):
        ent = dict(cfg=r["cfg"], d=r["d"], passed=False, fails=[])
        jt.cells.append(ent)
        if not (r["d"] == r["d"] and r["d"] > 0 and min_trades_ok(r["mt"])):
            ent["fails"].append("a/Δ")
            continue
        stat, cond = conditions(ev.get(Req(side, r["keys"], False)), ref, om, ncoins, nboot)
        cond["g_cat"] = (stat.z >= Z_CAT, "z %.2f" % stat.z)
        ent["fails"] = [k for k, v in cond.items() if k[0] != "_" and not v[0]]
        ent["passed"] = not ent["fails"]
        if ent["passed"] and jt.chosen is None:
            jt.chosen = r["cfg"]
            ent["stat"] = stat
    return jt


@dataclasses.dataclass
class Outcome:
    side: str
    months: tuple
    cfg1: dict
    steps1: list
    cfg2: dict
    steps2: list
    converged: bool
    joint: object
    final: dict
    m: int


def run_procedure(ev, spec, side, months, ncoins, provider=None, nboot=NBOOT):
    """Шаги 2–4 на месяцах `months`: проход 1 → проход 2 (принят хоть один шаг — «не сошлось», итог = база) → совместная сетка. Генератор → Outcome; в конце запрашивает ряд итога и базы."""
    om = month_days(months)
    cfg1, steps1 = yield from run_pass(ev, spec, side, {}, om, ncoins, provider, nboot)
    cfg2, steps2 = yield from run_pass(ev, spec, side, cfg1, om, ncoins, provider, nboot)
    converged = not any(s.accepted for s in steps2)
    m = sum(s.n_cells for s in steps1) + sum(s.n_cells for s in steps2)
    joint = Joint([])
    final = {}
    if converged:
        joint = yield from run_joint(ev, spec, side, cfg1, steps1, om, ncoins, nboot)
        m += joint.n_cells
        final = joint.chosen if joint.chosen is not None else cfg1
    yield from _need(ev, [Req(side, cfg_keys(spec, side, final), False), Req(side, cfg_keys(spec, side, {}), False)])
    return Outcome(side, tuple(months), cfg1, steps1, cfg2, steps2, converged, joint, final, m)


def procedure_gens(ev, spec, ncoins, provider=None, nboot=NBOOT, sides=SIDES):
    """Генераторы для drive(): на сторону — четыре свёртки проверки вперёд и полная (пять месяцев)."""
    g = {}
    for s in sides:
        for i, (tr, _) in enumerate(FOLDS, start=1):
            g["%s:f%d" % (s, i)] = run_procedure(ev, spec, s, tr, ncoins, provider, nboot)
        g["%s:full" % s] = run_procedure(ev, spec, s, (0, 1, 2, 3, 4), ncoins, provider, nboot)
    return g


# ---- проверка вперёд, расхождение выборов, собственный вердикт ---------------------------------------------------------------------

def cfg_ids(cfg):
    return {a: c["id"] for a, c in cfg.items()}


def choices_diverge(cfgs):
    """Расхождение выборов: по одной оси ≥ 2 разных принятых значений (не база) среди конфигураций. Возвращает {ось: [значения]}."""
    seen = {}
    for c in cfgs:
        for a, cell in c.items():
            seen.setdefault(a, set()).add(cell["id"])
    return {a: sorted(v) for a, v in seen.items() if len(v) > 1}


def wf_stats(ev, spec, side, outcomes, nboot=NBOOT):
    """Склейка четырёх месяцев проверки (105 сут.): Δ среднего $/сделку «конфигурация свёртки − база», односторонне 5 %, охрана суммы. outcomes: Outcome по свёрткам и полному."""
    folds = [outcomes["%s:f%d" % (side, i)] for i in range(1, len(FOLDS) + 1)]
    base = ev.get(Req(side, cfg_keys(spec, side, {}), False))
    Sc, Nc, Sr, Nr, Sb, Nb = [], [], [], [], [], []
    for (tr, te), o in zip(FOLDS, folds):
        ser = ev.get(Req(side, cfg_keys(spec, side, o.final), False))
        d = month_days([te])
        sc, nc = day_sums(ser)
        sr, nr = day_sums(base)
        Sc.append(sc[d]); Nc.append(nc[d]); Sr.append(sr[d]); Nr.append(nr[d])
    Sc, Nc, Sr, Nr = (np.concatenate(a) for a in (Sc, Nc, Sr, Nr))
    st = delta_stat(Sc, Nc, Sr, Nr, nboot)
    own = own_stat(Sc, Nc, nboot)
    guard = st.s_c > 0 and st.dsum >= -1.0 * st.se_sum
    full = outcomes["%s:full" % side]
    div = choices_diverge([o.final for o in folds] + [full.final])
    return dict(stat=st, own=own, confirmed=bool(st.lower > 0 and guard), guard=bool(guard), n_days=len(Sc), diverge=div,
                folds=[cfg_ids(o.final) for o in folds], full=cfg_ids(full.final), converged_full=full.converged,
                m=sum(o.m for o in folds) + full.m, S=Sc, N=Nc)


def wf_own_verdict(ev, spec, side, outcomes, ncoins, nboot=NBOOT):
    """«Сторона выключена», часть 1: собственный средний $/сделку конфигураций свёрток на склейке 105 сут. (m = 1, односторонне 5 %) по правилу §10 (устойчивость — суточная и по монетам склейки)."""
    S, N, cS, cN = [], [], np.zeros(ncoins), np.zeros(ncoins)
    for i, (tr, te) in enumerate(FOLDS, start=1):
        ser = ev.get(Req(side, cfg_keys(spec, side, outcomes["%s:f%d" % (side, i)].final), False))
        d = month_days([te])
        s_, n_ = day_sums(ser)
        S.append(s_[d]); N.append(n_[d])
        m = np.isin(ser.day, d)
        cS += np.bincount(ser.sym[m], weights=ser.val[m], minlength=ncoins)
        cN += np.bincount(ser.sym[m], minlength=ncoins)
    return own_verdict(np.concatenate(S), np.concatenate(N), cS, cN, nboot=nboot)


def freeze_decision(wf):
    """Исход до чтения 17–30.11: база, если Δ вперёд ≤ 0 или проход 2 на всём окне не сошёлся; иначе C* с пометкой (подтверждена / не подтверждена / расхождение)."""
    if wf["stat"].d <= 0 or not wf["converged_full"]:
        return "база", "Δ вперёд ≤ 0 или проход 2 не сошёлся"
    if wf["diverge"]:
        return "C*", "не поддержана: выборы свёрток расходятся %s" % wf["diverge"]
    return "C*", "процедура вперёд подтверждена" if wf["confirmed"] else "процедура вперёд не подтверждена"


def own_verdict(S, N, coin_S, coin_N, reject=None, nboot=NBOOT):
    """Собственный средний $ на сделку: «подтверждено» — Холм отверг H0 (reject; без него p < 0,05) и среднее > 0 без двух лучших суток и двух лучших монет;
    «опровергнуто» — среднее < 0 и без двух худших суток и без двух худших монет < 0; иначе «не ясно». coin_S/coin_N — суммы по монетам."""
    S, N = np.asarray(S, dtype=float), np.asarray(N, dtype=float)
    st = own_stat(S, N, nboot)
    cS, cN = np.asarray(coin_S, dtype=float), np.asarray(coin_N, dtype=float)

    def drop_days(order):
        k = order[:2]
        keep = np.ones(len(S), dtype=bool)
        keep[k] = False
        return ratio(S[keep].sum(), N[keep].sum())

    def drop_coins(order):
        keep = np.ones(len(cS), dtype=bool)
        keep[order[:2]] = False
        return ratio(cS[keep].sum(), cN[keep].sum())

    nb_d, nb_c = drop_days(np.argsort(-S, kind="stable")), drop_coins(np.argsort(-cS, kind="stable"))
    nw_d, nw_c = drop_days(np.argsort(S, kind="stable")), drop_coins(np.argsort(cS, kind="stable"))
    rej = (st.p < 0.05) if reject is None else reject
    ok = st.mean > 0 and rej and nb_d > 0 and nb_c > 0
    ref = st.mean < 0 and nw_d < 0 and nw_c < 0
    return dict(stat=st, no_best_days=nb_d, no_best_coins=nb_c, no_worst_days=nw_d, no_worst_coins=nw_c,
                verdict="подтверждено" if ok else ("опровергнуто" if ref else "не ясно"))


def side_off(own_wf, own_read):
    """«Сторона выключена»: собственное среднее на 105 сут. проверки (m = 1) И замороженной конфигурации на чтении не проходят правило §10 (подтверждено)."""
    return own_wf["verdict"] != "подтверждено" and own_read["verdict"] != "подтверждено"


if __name__ == "__main__":
    print(__doc__)
