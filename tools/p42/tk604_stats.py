#!/usr/bin/env python3
"""П-42 §13.26 (TK-604): ряды сделок, склейка окон, Δ $ на сделку, выбор клетки п. 6, проверка вперёд по месяцам п. 7. Только numpy, движок не запускает.

Формулы Δ, блочного бутстрепа, зерна, жадной устойчивости, склейки по зачётным диапазонам — как §13.24 «Ряды и статистика»
(`tools/p42/retitr_full.py` ветки `bb/tk-599-p42-retitration-full-v1-thr_6gt9kbmdir`, SHA 9b869adb; перенос функций без изменения формул, ветка не слита).

Чтения протокола (записаны в §13.26 п. 12 до счёта):
  — сосед плато (п. 6 г) — допущенная клетка (а1, а2, K «читается»); дубль и недопущенная клетка соседа не дают (как §13.24.1);
  — месяц информативен по числу сделок БАЗЫ, исключённых признаком (`excl`), а не по сделкам прогона клетки;
  — (а2) и информативность считаются по сделкам базы за обучающие сутки Ω; в п. 6 на всех 136 сут. Ω = все сутки.
"""
from __future__ import annotations

import csv
import dataclasses
import datetime
import hashlib
import io
import os
import zipfile

import numpy as np

DAY_MS = 86_400_000
BLOCK = 4
NBOOT = 20_000
D0 = datetime.date(2026, 7, 1)
LAST_DAY = datetime.date(2026, 11, 13)           # ни одной даты позже (17–30.11 не читается до заморозки, п. 8)
MONTH_NAMES = ("jul", "aug", "sep", "oct", "nov")
MONTH_LEN = (31, 31, 30, 31, 13)
NDAYS = sum(MONTH_LEN)                           # 136
MONTH_OF_DAY = np.repeat(np.arange(len(MONTH_LEN)), MONTH_LEN)
MIN_TRADES = (20, 20, 20, 20, 8)                 # п. 6 (а1): в месяц, у клетки и у эталона; 01–13.11 — 8
MIN_EXCL = 30                                    # п. 6 (а2): исключено сделок базы за Ω
MIN_EXCL_MONTH = 5                               # п. 6 (в): месяц информативен
Z_STEP = 2.0
PLATEAU = 0.5
JACCARD_MAX = 0.8
GRID_K = (20, 40, 60)
GRID_TAU = (0.02, 0.035, 0.05)
FOLDS = (((0,), 1), ((0, 1), 2), ((0, 1, 2), 3), ((0, 1, 2, 3), 4))     # (месяцы подбора, месяц проверки)
SIDES = ("L", "S")
ENGINE_SIDE = {"L": "long", "S": "short"}


class GuardError(Exception):
    """Охрана нарезки / сумм / окон / изоляции сработала — остановка, сообщение координатору."""


# ---- окна и изоляция ---------------------------------------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Window:
    name: str
    t0: str      # P42_T0 прогона
    lo: str      # зачётный диапазон по дате входа / сигнала, включительно
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

    @property
    def t0_ms(self):
        return int(datetime.datetime(self.t0d.year, self.t0d.month, self.t0d.day, tzinfo=datetime.timezone.utc).timestamp() * 1000)


# §13.26 п. 4: wa/wb/wc — геометрия §13.24.4 (TK-599); w0 — только история эпизодов 17–30.06 (сделок и кандидатов нет)
W0 = Window("w0", "2026-06-03", "2026-06-17", "2026-06-30")
WA = Window("wa", "2026-06-17", "2026-07-01", "2026-08-25")
WB = Window("wb", "2026-07-25", "2026-08-26", "2026-10-02")
WC = Window("wc", "2026-09-01", "2026-10-03", "2026-11-13")
TRADE_WINDOWS = (WA, WB, WC)
HIST_WINDOWS = (W0, WA, WB, WC)
ALLOWED_WINDOWS = ("w0", "wa", "wb", "wc")       # `wd` (17–30.11) — только командой чтения после заморозки; здесь отказ
GRID_START_DAY, COUNT_DAYS = 14, 60
TRADE_END = GRID_START_DAY + COUNT_DAYS - 1      # последние торговые сутки сетки (73)
LAST_MS = int(datetime.datetime(2026, 11, 14, tzinfo=datetime.timezone.utc).timestamp() * 1000)   # первая миллисекунда 14.11 — позже читать нельзя


def check_isolation(windows):
    """G6: в наборе окон — только w0/wa/wb/wc; зачёт и последние сутки данных (T0 + 73) не позже 13.11; иначе GuardError (в т. ч. `wd`)."""
    for w in windows:
        if w.name not in ALLOWED_WINDOWS:
            raise GuardError("изоляция: окно %r не разрешено (17–30.11 читается только командой после заморозки, п. 8)" % w.name)
        if w.hid > LAST_DAY or w.t0d + datetime.timedelta(days=TRADE_END) > LAST_DAY:
            raise GuardError("изоляция: окно %s (зачёт до %s, данные до %s) выходит за %s" % (w.name, w.hi, w.t0d + datetime.timedelta(days=TRADE_END), LAST_DAY))


def check_dates(t0, hi):
    """G6 для скриптов окна: зачёт до `hi` и данные до `t0 + 73` суток не позже 13.11 (строки ISO)."""
    t0d, hid = datetime.date.fromisoformat(t0), datetime.date.fromisoformat(hi)
    if hid > LAST_DAY or t0d + datetime.timedelta(days=TRADE_END) > LAST_DAY:
        raise GuardError("изоляция: зачёт до %s, данные до %s — позже %s" % (hid, t0d + datetime.timedelta(days=TRADE_END), LAST_DAY))


def check_abs_ms(abs_ms, what="время"):
    """G6: ни одной метки времени позже 13.11 24:00 UTC (абсолютные мс эпохи)."""
    a = np.asarray(abs_ms, dtype=np.int64)
    if a.size and int(a.max()) >= LAST_MS:
        raise GuardError("изоляция: %s %d мс позже 13.11 24:00 UTC (данные 14.11 и позже не принимаются)" % (what, int(a.max())))


def check_windows(windows=TRADE_WINDOWS):
    """Диапазоны смыкаются без наложений и пропусков, покрывают 01.07–13.11 (136 суток), зачёт внутри торговой части окна."""
    check_isolation(windows)
    prev_hi = None
    for w in windows:
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


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


# ---- сделки окна, склейка ----------------------------------------------------------------------------------------------------

@dataclasses.dataclass
class TradeTable:
    sym: list
    sig_ms: np.ndarray       # мс от T0 окна
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
    """rows: словари с полями `<side>-trades.csv` (sym, sig_ms, entry_ms, exit_ms, gross, fees, funding, net, kind)."""
    f = lambda k: np.array([float(r[k]) for r in rows], dtype=float)           # noqa: E731
    i = lambda k: np.array([int(r[k]) for r in rows], dtype=np.int64)          # noqa: E731
    return TradeTable([r["sym"] for r in rows], i("sig_ms"), i("entry_ms"), i("exit_ms"), f("gross"), f("fees"), f("funding"), f("net"), [r["kind"] for r in rows])


def read_trades_csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        return table_from_rows(list(csv.DictReader(f)))


def read_daily_csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        return {int(r["day"]): float(r["pnl"]) for r in csv.DictReader(f)}


def abs_day(win, entry_ms):
    """Индекс суток от 2026-07-01 по дате входа: T0 окна + entry_ms // сутки."""
    return (win.t0d - D0).days + np.asarray(entry_ms, dtype=np.int64) // DAY_MS


@dataclasses.dataclass
class Series:
    """Сделки конфигурации на 01.07–13.11: все массивы одной длины, порядок — по окнам и времени входа."""
    day: np.ndarray       # индекс суток 0…135
    sym: np.ndarray       # индекс монеты в таблице монет
    val: np.ndarray       # gross − fees (метрика отбора, без фандинга)
    key_ms: np.ndarray    # абсолютное время сигнала кандидата (мс эпохи) — ключ (sym, key_ms) сделки базы
    sym_name: list = None
    t_out: np.ndarray = None   # абсолютное время выхода (мс эпохи): для «прошлых сделок монеты» (ступень 1)
    t_in: np.ndarray = None    # абсолютное время входа (мс эпохи): граница фандинга
    fund: np.ndarray = None    # фандинг движка (сентябрь–ноябрь; справочно, в метрику не входит)

    def __len__(self):
        return len(self.day)


def make_series(day, sym, val, key_ms=None, sym_name=None, t_out=None, t_in=None, fund=None):
    day = np.asarray(day, dtype=np.int64)
    n = len(day)
    km = np.zeros(n, dtype=np.int64) if key_ms is None else np.asarray(key_ms, dtype=np.int64)
    a = lambda x: None if x is None else np.asarray(x)                              # noqa: E731
    return Series(day, np.asarray(sym, dtype=np.int64), np.asarray(val, dtype=float), km, sym_name, a(t_out), a(t_in), a(fund))


def splice(tables, coins, windows=TRADE_WINDOWS, daily=None):
    """Склейка окон → Series. tables: {окно: TradeTable}; daily: {окно: {строка: pnl}} (G7: Σ daily среза = Σ net среза).
    Охрана: net = gross − fees + funding по строкам; Σ(gross − fees) склейки = Σ по зачётным строкам; диапазоны смыкаются; ни одной сделки позже 13.11."""
    check_windows(windows)
    ci = {c: i for i, c in enumerate(coins)}
    parts, tot_val = [], 0.0
    for w in windows:
        t = tables[w.name]
        d = abs_day(w, t.entry_ms)
        if len(t) and np.abs(t.net - (t.gross - t.fees + t.funding)).max() > 2e-6:
            raise GuardError("%s: net ≠ gross − fees + funding в строке сделки" % w.name)
        lo_i, hi_i = (w.lod - D0).days, (w.hid - D0).days
        sel = np.nonzero((d >= lo_i) & (d <= hi_i))[0]
        unknown = sorted({t.sym[i] for i in sel if t.sym[i] not in ci})
        if unknown:
            raise GuardError("%s: монеты вне таблицы: %s" % (w.name, ", ".join(unknown[:5])))
        val = (t.gross - t.fees)[sel]
        tot_val += float(val.sum())
        if daily is not None and daily.get(w.name) is not None:
            rows = daily[w.name]
            first, last = (w.lod - w.t0d).days - GRID_START_DAY, (w.hid - w.t0d).days - GRID_START_DAY    # строка daily = сутки сетки − 14
            ssum = sum(rows.get(i, 0.0) for i in range(first, last + 1))
            nsum = float(t.net[sel].sum())
            tol = max(1e-4, 2e-6 * len(sel))
            if abs(ssum - nsum) > tol:
                raise GuardError("%s: Σ daily среза %+.6f ≠ Σ net сделок среза %+.6f (допуск %.1e)" % (w.name, ssum, nsum, tol))
        parts.append((d[sel], np.array([ci[t.sym[i]] for i in sel], dtype=np.int64), val, t.sig_ms[sel] + w.t0_ms, [t.sym[i] for i in sel], t.exit_ms[sel] + w.t0_ms,
                      t.entry_ms[sel] + w.t0_ms, t.funding[sel]))
    day = np.concatenate([p[0] for p in parts]) if parts else np.zeros(0, dtype=np.int64)
    sym = np.concatenate([p[1] for p in parts])
    val = np.concatenate([p[2] for p in parts])
    key = np.concatenate([p[3] for p in parts])
    t_out = np.concatenate([p[5] for p in parts])
    t_in = np.concatenate([p[6] for p in parts])
    fund = np.concatenate([p[7] for p in parts])
    if abs(float(val.sum()) - tot_val) > 1e-6 * max(1, len(val)):
        raise GuardError("Σ склеенного ряда %+.6f ≠ Σ (gross − fees) зачётных строк %+.6f" % (float(val.sum()), tot_val))
    if len(day) and (day.min() < 0 or day.max() >= NDAYS):
        raise GuardError("сутки вне 0…%d после склейки" % (NDAYS - 1))
    check_abs_ms(key, "время сигнала сделки")
    return make_series(day, sym, val, key, [s for p in parts for s in p[4]], t_out, t_in, fund)


NOTIONAL = 500.0


def funding_bound(sym_names, t_in, t_out, rates, notional=NOTIONAL):
    """B = Σ|ставка|·$500 по выплатам, пересечённым сделкой (t_in < выплата ≤ t_out), июль–август (§13.24 «Фандинг», как §13.22 D1). rates: {монета: (времена мс по возрастанию, |ставки|)}."""
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


def load_funding_rates(root, syms, months=("2026-07", "2026-08")):
    """{монета: (времена, |ставки|)} из `<root>/<SYM>/<SYM>-fundingRate-<YYYY-MM>.zip`; нет файла — пропуск."""
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


def _keyset(t, w, lo, hi):
    d = abs_day(w, t.entry_ms)
    a, b = (datetime.date.fromisoformat(lo) - D0).days, (datetime.date.fromisoformat(hi) - D0).days
    return {(t.sym[i], int(t.entry_ms[i]), int(t.exit_ms[i])): (round(float(t.gross[i]), 6), round(float(t.fees[i]), 6)) for i in np.nonzero((d >= a) & (d <= b))[0]}


def tables_equal(ta, tb, w):
    """G2: сделки двух прогонов одного окна на зачётном диапазоне равны по (монета, вход, выход, gross, fees). Возвращает (равны, число сделок, число различий)."""
    A, B = _keyset(ta, w, w.lo, w.hi), _keyset(tb, w, w.lo, w.hi)
    diff = len(set(A) ^ set(B)) + sum(1 for k in set(A) & set(B) if abs(A[k][0] - B[k][0]) > 1e-6 or abs(A[k][1] - B[k][1]) > 1e-6)
    return diff == 0, len(A), diff


# ---- Δ $ на сделку, бутстреп -------------------------------------------------------------------------------------------------

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
    p: float            # односторонний p (H0: Δ ≤ 0) = (1 + #(бутстреп Δ ≤ 0)) / (1 + повторов)
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
    p = (1 + int((bd <= 0).sum())) / (1 + len(bd))
    return DStat(d, se, _z(d, se), float(np.percentile(bd, 5)), float(p), float(Sc.sum() - Sr.sum()), float(bs.std(ddof=1)),
                 float(Sc.sum()), float(Sr.sum()), int(Nc.sum()), int(Nr.sum()), n)


def holm_reject(ps, alpha=0.05):
    m = len(ps)
    rej = [False] * m
    for k, i in enumerate(sorted(range(m), key=lambda j: ps[j]), start=1):
        if ps[i] <= alpha / (m - k + 1):
            rej[i] = True
        else:
            break
    return rej


# ---- месяцы, минимум сделок, знак по месяцам, устойчивость -------------------------------------------------------------------

def month_days(months):
    return np.nonzero(np.isin(MONTH_OF_DAY, list(months)))[0]


def need_months(m):
    """Месяцев с Δ > 0 из m информативных: 2 из 2, 2 из 3, 3 из 4, 4 из 5; m < 2 — не выполнено (None)."""
    return {2: 2, 3: 2, 4: 3, 5: 4}.get(m)


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


def _coin_arrays(ser, inom, ncoins):
    m = inom[ser.day]
    return np.bincount(ser.sym[m], weights=ser.val[m], minlength=ncoins), np.bincount(ser.sym[m], minlength=ncoins).astype(float)


def robust(c, r, om, ncoins):
    """Устойчивость: Δ > 0 после жадного удаления двух суток и (отдельно) двух монет. Возвращает (ок, Δ без суток, Δ без монет)."""
    Sc, Nc = day_sums(c)
    Sr, Nr = day_sums(r)
    dd = _greedy(Sc[om], Nc[om], Sr[om], Nr[om])
    inom = np.zeros(NDAYS, dtype=bool)
    inom[om] = True
    (a, b), (e, f) = _coin_arrays(c, inom, ncoins), _coin_arrays(r, inom, ncoins)
    dc = _greedy(a, b, e, f)
    return bool(dd is not None and dc is not None and dd > 0 and dc > 0), dd, dc


def robust_negative(c, r, om, ncoins):
    """Зеркало для «не отделяет» (п. 7): Δ < 0 после удаления двух худших суток и двух худших монет (те, чьё удаление поднимает Δ сильнее всего). Возвращает (ок, Δ без суток, Δ без монет)."""
    Sc, Nc = day_sums(c)
    Sr, Nr = day_sums(r)
    g = _greedy(Sr[om], Nr[om], Sc[om], Nc[om])             # (r − c) минимально ⇔ (c − r) максимально
    dd = None if g is None else -g
    inom = np.zeros(NDAYS, dtype=bool)
    inom[om] = True
    (a, b), (e, f) = _coin_arrays(c, inom, ncoins), _coin_arrays(r, inom, ncoins)
    gc = _greedy(e, f, a, b)
    dc = None if gc is None else -gc
    return bool(dd is not None and dc is not None and dd < 0 and dc < 0), dd, dc


# ---- клетки и выбор п. 6 -----------------------------------------------------------------------------------------------------

def cell_id(K, tau):
    return "K%d_t%03d" % (K, int(round(tau * 1000)))


GRID = tuple((K, t) for K in GRID_K for t in GRID_TAU)          # порядок: K по возрастанию, внутри — τ по возрастанию


@dataclasses.dataclass
class CellRun:
    cid: str
    K: int
    tau: float
    series: Series | None          # прогон клетки с фильтром; None у дубля
    excl: np.ndarray               # булев массив по сделкам базы: исключена признаком (n ≥ K и bk < τ)
    dup_of: str | None = None


@dataclasses.dataclass
class SideData:
    base: Series
    cells: list                    # CellRun в порядке сетки (включая дубли)
    readable: dict                 # K → читается (хотя бы в одном месяце ≥ 5 % кандидатов со значением)
    ncoins: int


def lattice_neighbours(cid, cells):
    """Клетки, соседние по ребру в решётке K × τ (дубль не сосед)."""
    pos = {(c.K, c.tau): c for c in cells}
    iK, it = GRID_K.index(next(c.K for c in cells if c.cid == cid)), GRID_TAU.index(next(c.tau for c in cells if c.cid == cid))
    out = []
    for dk, dt in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        a, b = iK + dk, it + dt
        if 0 <= a < len(GRID_K) and 0 <= b < len(GRID_TAU):
            c = pos.get((GRID_K[a], GRID_TAU[b]))
            if c is not None and c.dup_of is None and c.series is not None:
                out.append(c)
    return out


def jaccard(a, b):
    """Jaccard булевых масок (множества исключённых сделок базы в Ω); пусто и пусто — 0."""
    u = int(np.count_nonzero(a | b))
    return float(np.count_nonzero(a & b)) / u if u else 0.0


@dataclasses.dataclass
class Selection:
    winner: str | None
    rows: list
    detail: dict
    stat: DStat | None
    accepted: bool
    reasons: list


def _omask(om):
    m = np.zeros(NDAYS, dtype=bool)
    m[om] = True
    return m


def cell_row(side, cell, om):
    """Строка клетки на Ω: Δ, число сделок, число исключённых базы (всего и по месяцам), допуск (а1), (а2), K читается."""
    base = side.base
    Sc, Nc = day_sums(cell.series)
    Sr, Nr = day_sums(base)
    mt = month_table(Sc, Nc, Sr, Nr, om)
    nc, nr = Nc[om].sum(), Nr[om].sum()
    d = ratio(Sc[om].sum(), nc) - ratio(Sr[om].sum(), nr) if nc > 0 and nr > 0 else float("nan")
    inom = _omask(om)
    ex = cell.excl & inom[base.day]
    n_ex = int(ex.sum())
    ex_m = {k: int((ex & (MONTH_OF_DAY[base.day] == k)).sum()) for k in sorted(set(MONTH_OF_DAY[om].tolist()))}
    a1 = bool(min_trades_ok(mt))
    a2 = n_ex >= MIN_EXCL
    rd = bool(side.readable.get(cell.K, False))
    return dict(cid=cell.cid, K=cell.K, tau=cell.tau, d=d, n_c=int(nc), n_r=int(nr), n_ex=n_ex, ex_month=ex_m, month=mt,
                a1=a1, a2=a2, readable=rd, admitted=bool(a1 and a2 and rd and d == d), mask=ex)


def select_cell(side, om, nboot=NBOOT):
    """Правило п. 6 на Ω (дни om): допуск → победитель (наибольшая Δ > 0, только он) → (б)…(е). Не принята → winner = None (второй по Δ не пробуется)."""
    om = np.asarray(om)
    live = [c for c in side.cells if c.dup_of is None and c.series is not None]
    rows = {c.cid: cell_row(side, c, om) for c in live}
    pool = [r for r in rows.values() if r["admitted"]]
    out_rows = [{k: v for k, v in r.items() if k != "mask"} for r in rows.values()]
    if not pool:
        return Selection(None, out_rows, {}, None, False, ["нет допущенных клеток (а1/а2/K читается)"])
    win = max(pool, key=lambda r: (round(r["d"], 9), -[c.cid for c in live].index(r["cid"])))
    if not win["d"] > 0:
        return Selection(None, out_rows, {}, None, False, ["победителя нет: наибольшая Δ %+.4f ≤ 0 (%s)" % (win["d"], win["cid"])])
    cell = next(c for c in live if c.cid == win["cid"])
    base = side.base
    Sc, Nc = day_sums(cell.series)
    Sr, Nr = day_sums(base)
    st = delta_stat(Sc[om], Nc[om], Sr[om], Nr[om], nboot)
    detail = {}
    detail["b_z"] = (st.z >= Z_STEP, "z %.2f" % st.z)
    inf = [k for k, n in win["ex_month"].items() if n >= MIN_EXCL_MONTH]
    need = need_months(len(inf))
    pos = sum(1 for k in inf if win["month"][k][0] > 0)
    detail["c_months"] = (need is not None and pos >= need, "информативных месяцев %d, с Δ > 0: %d, нужно %s; Δ по месяцам %s" % (
        len(inf), pos, need, {MONTH_NAMES[k]: round(v[0], 4) for k, v in win["month"].items()}))
    # (г) плато
    good = []
    det = {}
    for q in lattice_neighbours(win["cid"], side.cells):
        rq = rows[q.cid]
        j = jaccard(win["mask"], rq["mask"])
        det[q.cid] = (round(rq["d"], 4), round(j, 3), rq["admitted"])
        if rq["admitted"] and rq["d"] >= PLATEAU * win["d"] and j <= JACCARD_MAX:
            good.append(q.cid)
    detail["g_plateau"] = (len(good) >= 2, "соседи (Δ, Jaccard, допущен) %s; порог Δ %.4f; зачтены %s" % (det, PLATEAU * win["d"], good))
    ok, dd, dc = robust(cell.series, base, om, side.ncoins)
    detail["d_robust"] = (ok, "без 2 суток %s, без 2 монет %s" % (dd, dc))
    detail["e_sum"] = (st.s_c > 0 and st.dsum >= -1.0 * st.se_sum, "ΣS %+.2f, ΔΣ %+.2f, SE_Σ %.2f" % (st.s_c, st.dsum, st.se_sum))
    fails = [k for k, v in detail.items() if not v[0]]
    return Selection(win["cid"], out_rows, detail, st, not fails, ["%s: %s" % (k, detail[k][1]) for k in fails])


def chosen(sel):
    """Клетка, принятая правилом п. 6 (cid) или None («нет фильтра»)."""
    return sel.winner if sel.accepted else None


# ---- проверка вперёд п. 7 ----------------------------------------------------------------------------------------------------

def _pick(ser, days):
    m = np.isin(ser.day, days)
    return Series(ser.day[m], ser.sym[m], ser.val[m], ser.key_ms[m], None)


def _concat(parts):
    return Series(np.concatenate([p.day for p in parts]), np.concatenate([p.sym for p in parts]), np.concatenate([p.val for p in parts]),
                  np.concatenate([p.key_ms for p in parts]), None)


def walk_forward(side, nboot=NBOOT):
    """Четыре расширяющиеся свёртки п. 7: на обучающих месяцах — п. 6 целиком; месяц проверки — ряд выбора (клетка или база). Склейка 105 суток, Δ «выбор − база»,
    односторонне 5 % (p — для Холма по двум сторонам снаружи), охрана суммы, устойчивость (без 2 лучших суток / монет) и зеркало (без 2 худших)."""
    folds, sel_parts, base_parts, test_days = [], [], [], []
    for tr, te in FOLDS:
        om = month_days(tr)
        sel = select_cell(side, om, nboot)
        cid = chosen(sel)
        d = month_days([te])
        cell = next((c for c in side.cells if c.cid == cid), None) if cid else None
        sel_parts.append(_pick(cell.series if cell else side.base, d))
        base_parts.append(_pick(side.base, d))
        test_days.append(d)
        folds.append(dict(train=tr, test=te, cid=cid, winner=sel.winner, reasons=sel.reasons, rows=sel.rows, detail=sel.detail,
                          stat=sel.stat, active_days=int(len(d) if cid else 0)))
    choice, base = _concat(sel_parts), _concat(base_parts)
    days = np.concatenate(test_days)                                   # в порядке месяцев: Aug, Sep, Oct, Nov(1–13) — 105 суток
    pos = {int(dd): i for i, dd in enumerate(days)}
    c2 = Series(np.array([pos[int(x)] for x in choice.day], dtype=np.int64), choice.sym, choice.val, choice.key_ms, None)
    r2 = Series(np.array([pos[int(x)] for x in base.day], dtype=np.int64), base.sym, base.val, base.key_ms, None)
    n = len(days)
    Sc, Nc = np.bincount(c2.day, weights=c2.val, minlength=n), np.bincount(c2.day, minlength=n).astype(float)
    Sr, Nr = np.bincount(r2.day, weights=r2.val, minlength=n), np.bincount(r2.day, minlength=n).astype(float)
    none = all(f["cid"] is None for f in folds)
    stat = delta_stat(Sc, Nc, Sr, Nr, nboot)
    nc_ = side.ncoins

    def coins(s):
        return np.bincount(s.sym, weights=s.val, minlength=nc_), np.bincount(s.sym, minlength=nc_).astype(float)
    (ca, cb), (cc, ce) = coins(c2), coins(r2)
    dd = _greedy(Sc, Nc, Sr, Nr)
    dc = _greedy(ca, cb, cc, ce)
    gd, gc = _greedy(Sr, Nr, Sc, Nc), _greedy(cc, ce, ca, cb)
    rob = bool(dd is not None and dc is not None and dd > 0 and dc > 0)
    neg = bool(gd is not None and gc is not None and -gd < 0 and -gc < 0)
    guard = stat.s_c > 0 and stat.dsum >= -1.0 * stat.se_sum
    return dict(folds=folds, stat=stat, p=stat.p, none=none, n_days=n, active_days=int(sum(f["active_days"] for f in folds)),
                robust=rob, no_best_days=dd, no_best_coins=dc, neg_robust=neg, no_worst_days=None if gd is None else -gd,
                no_worst_coins=None if gc is None else -gc, guard=bool(guard), S=Sc, N=Nc, Sr=Sr, Nr=Nr, mean_sel=ratio(Sc.sum(), Nc.sum()),
                mean_base=ratio(Sr.sum(), Nr.sum()))


def verdicts(wf):
    """Исход стороны по §13.26 п. 7–8; wf — {"L": walk_forward, "S": walk_forward}. Холм m = 2 (обе стороны всегда), односторонне 5 %."""
    ps = [wf[s]["p"] for s in SIDES]
    rej = holm_reject(ps, 0.05)
    out = {}
    for s, r in zip(SIDES, rej):
        w = wf[s]
        if w["none"]:
            out[s] = ("не ясно — процедура не выбрала фильтр", False)
        elif r and w["stat"].d > 0 and w["robust"] and w["guard"]:
            out[s] = ("подтверждено", True)
        elif w["stat"].d < 0 and w["neg_robust"]:
            out[s] = ("не отделяет", False)
        else:
            out[s] = ("не подтверждено", False)
    return out


# ---- мощность (п. 9) ---------------------------------------------------------------------------------------------------------

def mde_cell(sigma, n_cov, f_cov, f_all, coef=2.49):
    """Минимальная различимая разница средних исключённых и оставшихся среди покрытых `d_min` и по всем сделкам `MDE_Δ = f_all·d_min` (п. 9; coef 2,49 / Холм 2,80)."""
    if n_cov <= 0 or not 0 < f_cov < 1:
        return float("nan"), float("nan")
    d_min = coef * sigma / (f_cov * (1 - f_cov) * n_cov) ** 0.5
    return float(d_min), float(f_all * d_min)
