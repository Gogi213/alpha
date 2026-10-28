#!/usr/bin/env python3
"""TK-422: счётчик признаков пампа/дампа по окну. Описание признаков — docs/findings/pump-features-2026-10-27.md.
Использование: tk422-features.py <окна.csv> <выход.csv> [--candles /srv/alpha-sda/candles-1m] [--tk415 /srv/alpha-sda/tk415/out]
               [--trades /data/tk417] [--aug-candles /srv/alpha-sda/tk423/candles] [--ob биржа/рынок=корень[,…]] [--instr bybit-instruments.tsv]
               [--grid grid.json] [--procs N] [--cube куб свечей tk436-cube.py] [--cube-trades куб сделок: свечи/сделки вместо разбора csv.gz] [--cube-from ГГГГ-ММ-ДДTЧЧ:ММ: куб виден с этой минуты]
Окна: колонки биржа|exchange, рынок|market, инструмент|монета|symbol, t_start, t_peak, t_end (ISO-минута UTC или мс), [t_pre], [знак|sign: памп/дамп/+1/-1].
Фазы (P-TK421 v3): pre [t_pre,t_start), leg [t_start,t_peak], post (t_peak,t_end]; t_pre = t_start − (t_end − t_start), если не подан. Имя признака = <признак>_<pre|leg|post|ctx>;
ctx = контекст до старта по лукбэку H/суток (не фаза). На каждый признак — cov_<признак> ∈ {ok, нет_данных, не_применимо}.
Нет знака — по цене (конец пика против начала). Сырьё не сохраняется: свечи 1 м, OI/фандинг/ликвидации и сделки → строка на окно.
Все ценовые признаки считаются в «ориентации пампа»: для дампа цена берётся с минусом (high<->low), поэтому «вверх» = по ходу события.
Расстояния — bps от цены открытия минуты старта. Пустое значение = данных нет (не 0). Только ядро Python, без numpy."""
import csv, gzip, json, math, os, re, sys, time
from bisect import bisect_left, bisect_right
from datetime import datetime, timezone
from multiprocessing import Pool

GRID = {
    "H": [60, 240, 1440],          # глубина пула ликвидности назад, мин
    "K": [1, 2],                   # половина-ширина фрактала (1 = 3 свечи)
    "N": [5, 30],                  # возврат телом: за сколько минут после пика
    "T": [0, 10, 25],              # закрепление: на сколько bps закрытие за уровнем
    "R": [60, 180, 360],           # диапазон накопления AMD, мин
    "KM": [5, 15],                 # длина окна выноса после старта, мин
    "FVGMIN": [0, 5, 10],          # минимум размера FVG, bps
    "RET": [0.3, 0.5, 0.618],      # уровни откатной ноги (0,786 снят: retrace_depth отдаётся непрерывно)
    "ABS_N": [5, 15],              # окно у пика для поглощения, мин (пороги imb/surge/disp — непрерывные колонки, их назначает шаг 3)
}
AUG = [None]   # доп. корень свечей (август) — склеивается с основным
CUBE = [None, None]   # [куб свечей tk436-cube.py, куб сделок] (--cube / --cube-trades): вместо разбора csv.gz; numpy — только здесь
CUBE_FROM = [0]       # --cube-from: первая минута, с которой куб виден (запас PAD раньше прежнего источника меняет data_age_days/hist7); 0 — весь куб
_cm = {}              # кэш memmap кубов: (куб, имя) -> (массив, [инструменты], lo)
OBR = {}       # (биржа, рынок) → корни минутных срезов стакана; чужой рынок подставлять нельзя
LAUNCH = {}    # Bybit symbol -> launch_ms
NB = os.environ.get("TK422_NUMBA") == "1"  # TK-441: numba-ядра corr/fractals (умолчание — прежний python); sum() Py3.13 повторён побитно (Neumaier)
if NB:
    import numpy as np
    from numba import njit

    @njit(cache=True)
    def _nsum(a):
        f = 0.0 + a[0]  # CPython: 0 + x0, далее Kahan–Babuška–Neumaier, в конце + c при c != 0 и конечном
        c = 0.0
        for i in range(1, a.shape[0]):
            x = a[i]
            t = f + x
            if abs(f) >= abs(x):
                c += (f - t) + x
            else:
                c += (x - t) + f
            f = t
        if c != 0.0 and math.isfinite(c):
            f += c
        return f

    @njit(cache=True)
    def _ncorr(x, y):
        n = x.shape[0]
        mx = _nsum(x) / n
        my = _nsum(y) / n
        dx = x - mx
        dy = y - my
        sxx = _nsum(dx * dx)
        syy = _nsum(dy * dy)
        if sxx <= 0 or syy <= 0:
            return np.nan
        return _nsum(dx * dy) / math.sqrt(sxx * syy)

    @njit(cache=True)
    def _nfr(xs, k, hi):
        n = xs.shape[0]
        out = []
        for i in range(k, n - k):
            x = xs[i]
            ok = True
            for j in range(i - k, i + k + 1):
                if j != i and not (x > xs[j] if hi else x < xs[j]):
                    ok = False
                    break
            if ok:
                out.append(i)
        return out
BASE = 1440  # минут нормы
NEG_INF = -(1 << 62)  # нижняя граница среза sub()


def tms(s):
    s = s.strip()
    if s.isdigit():
        return int(s)
    return int(datetime.strptime(s[:16], "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc).timestamp() * 1000)


def col(h, *names):
    for n in names:
        if n in h:
            return n
    return None


def fnum(x):
    return "" if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))) else (round(x, 6) if isinstance(x, float) else x)


def med(x):
    y = sorted(x)
    return y[len(y) // 2] if y else None


_cc = {}
RNG_ON = [False]   # --cube-range 1: загружать свечи группы окном минут (умолчание — весь ряд, как раньше)
RNG = [None]   # (мин. минута, макс. минута) загрузки свечей группы: [min(ms, mpre) − 8 сут, max(me) + 3 сут]; None — весь ряд


def cube_open(cube, name):
    if (cube, name) not in _cm:
        import numpy as np
        _cm[(cube, name)] = (np.load("%s/%s.npy" % (cube, name), mmap_mode="r"), open("%s/%s.syms" % (cube, name)).read().split(),
                             int(open("%s/%s.lo" % (cube, name)).read()))
    return _cm[(cube, name)]


class Cd(dict):
    """словарь свечей окна минут группы; first — первая валидная минута всего ряда"""
    first = None


def dmin(d):
    """min(ключей) словаря свечей: у урезанного по окну словаря — первая минута всего ряда"""
    d = d.d if isinstance(d, Neg) else d
    return d.first if isinstance(d, Cd) and d.first is not None else min(d)


def candles_cube(ex, mk, sym):
    """{минута: (o,h,l,c,q)} из куба — те же значения, что csv.gz (куб пропускает строки с close<=0)"""
    mm, syms, lo = cube_open(CUBE[0], "%s-%s" % (ex, mk))
    if sym not in syms:
        return {}
    x = mm[syms.index(sym)]
    ok = ~(x[3] != x[3])
    if CUBE_FROM[0] > lo:
        ok[:CUBE_FROM[0] - lo] = False
    ix = ok.nonzero()[0]
    first = int(ix[0]) + lo if len(ix) else None  # первая валидная минута ВСЕГО ряда (data_age_days, htf) — не окна
    if RNG[0] and sym not in ("BTCUSDT", "BTC_USDT"):  # BTC общий для групп — целиком
        ix = ix[(ix + lo >= RNG[0][0]) & (ix + lo <= RNG[0][1])]
    v = x[:, ix].tolist()
    d = Cd(zip((ix + lo).tolist(), zip(v[0], v[1], v[2], v[3], v[4])))
    d.first = first
    return d


def candles(root, ex, mk, sym):
    key = (ex, mk, sym)
    if key not in _cc and CUBE[0]:
        _cc[key] = candles_cube(ex, mk, sym)
    if key not in _cc:
        d = {}
        for r in (AUG[0], root):  # август под сентябрём: одни и те же минуты не пересекаются
            fn = "%s/%s-%s/%s.csv.gz" % (r, ex, mk, sym) if r else None
            if fn and os.path.exists(fn):
                with gzip.open(fn, "rt") as f:
                    next(f)
                    for line in f:
                        p = line.split(",")
                        d[int(p[0]) // 60000] = (float(p[1]), float(p[2]), float(p[3]), float(p[4]), float(p[6]))
        _cc[key] = d
    return _cc[key]


def spot_name(ex, sym):
    return sym.replace("_", "") if ex == "bybit" else sym


def btc_name(ex):
    return "BTCUSDT" if ex == "bybit" else "BTC_USDT"


def fut_mkt(ex):
    return "linear" if ex == "bybit" else "futures_usdt"


_or = {}


class Neg:
    """дамп-ориентация свечей без копии словаря: (o,h,l,c,q) -> (-o,-l,-h,-c,q) на обращении (TK-439: копия всего месяца на окно стоила 11 из 70 с)"""
    __slots__ = ("d",)

    def __init__(self, d):
        self.d = d

    def __getitem__(self, m):
        v = self.d[m]
        return (-v[0], -v[2], -v[1], -v[3], v[4])

    def get(self, m, default=None):
        v = self.d.get(m)
        return default if v is None else (-v[0], -v[2], -v[1], -v[3], v[4])

    def __contains__(self, m):
        return m in self.d

    def __iter__(self):
        return iter(self.d)

    def __len__(self):
        return len(self.d)


def orient(d, s):
    """минута -> (o,h,l,c,q) в ориентации пампа; дамп-ориентация — ленивая обёртка над словарём свечей (кэш до конца группы)"""
    if s > 0:
        return d
    k = (id(d), s)
    if k not in _or:
        _or[k] = (d, Neg(d))  # d в значении — id не переиспользуется
    return _or[k][1]


_sq = {}


def _sqe(d):
    k = id(d)
    e = _sq.get(k)
    if e is None or e[0] is not d or e[1] != len(d):
        src = d.d if isinstance(d, Neg) else d
        ks = sorted(src)
        vs = [src[m] for m in ks]
        if isinstance(d, Neg):
            vs = [(-v[0], -v[2], -v[1], -v[3], v[4]) for v in vs]
        e = _sq[k] = (d, len(d), ks, vs)  # d в значении — id не переиспользуется
    return e


def seq(d, a, b):
    """[(минута, свеча)] по возрастанию минут в [a, b); отсортированный вид словаря строится один раз на группу (TK-439: циклом по минутам — 5 из 24 с профиля)"""
    e = _sqe(d)
    ks, vs = e[2], e[3]
    i, j = bisect_left(ks, a), bisect_left(ks, b)
    return list(zip(ks[i:j], vs[i:j]))


_sx = {}


def hl(d, a, b):
    """(число минут, max high, min low) в [a, b); None, None при пустом. TK-441: за TK422_NUMBA — срезы numpy вместо генераторов по seq (max/min точные, значения те же float)"""
    if not NB:
        s = seq(d, a, b)
        return len(s), (max(v[1] for _, v in s) if s else None), (min(v[2] for _, v in s) if s else None)
    e = _sqe(d)
    x = _sx.get(id(d))
    if x is None or x[0] is not e:
        vs = e[3]
        x = _sx[id(d)] = (e, np.fromiter((v[1] for v in vs), np.float64, len(vs)), np.fromiter((v[2] for v in vs), np.float64, len(vs)))
    ks = e[2]
    i, j = bisect_left(ks, a), bisect_left(ks, b)
    if j <= i:
        return 0, None, None
    return j - i, float(x[1][i:j].max()), float(x[2][i:j].min())


_dy = {}


def day_ent(d, a, b):
    """(максимум high, число минут) серии d на [a, b); None — минут нет; память на группу, очищается вместе с _sq (TK-441)"""
    k = (id(d), a, b)
    r = _dy.get(k)
    if r is None or r[0] is not d:
        sq = seq(d, a, b)
        r = _dy[k] = (d, (max(v[1] for _, v in sq), len(sq)) if sq else None)
    return r[1]


def fractals(bars, k, hi=True):
    """индексы фракталов в списке bars [(m,(o,h,l,c,q))]; фрактал i подтверждён к бару i+k (TK-441: колонка high/low списком, срезы вместо индексов)"""
    out = []
    j = 1 if hi else 2
    xs = [b[1][j] for b in bars]
    if NB:
        return list(_nfr(np.array(xs, dtype=np.float64), k, hi))
    for i in range(k, len(bars) - k):
        x = xs[i]
        if hi:
            ok = all(x > y for y in xs[i - k : i]) and all(x > y for y in xs[i + 1 : i + k + 1])
        else:
            ok = all(x < y for y in xs[i - k : i]) and all(x < y for y in xs[i + 1 : i + k + 1])
        if ok:
            out.append(i)
    return out


def bps(a, b, p0):
    return (b - a) / p0 * 1e4


def ret_close(d, mp, level, N, T, p0):
    """после пика mp: есть ли закрытие ниже level на T bps в течение N минут"""
    thr = level - T / 1e4 * p0
    g = d.get
    for m in range(mp + 1, mp + N + 1):
        v = g(m)
        if v is not None and v[3] <= thr:
            return 1
    return 0


def corr(x, y):
    n = len(x)
    if n < 5:
        return None
    if NB:
        r = _ncorr(np.array(x, dtype=np.float64), np.array(y, dtype=np.float64))
        return None if r != r else float(r)
    mx, my = sum(x) / n, sum(y) / n
    dx = [a - mx for a in x]
    dy = [b - my for b in y]
    sxx = sum([a ** 2 for a in dx])  # списки, не генераторы: те же слагаемые в том же порядке -> sum() Py3.13 побитно прежний (TK-441)
    syy = sum([b ** 2 for b in dy])
    if sxx <= 0 or syy <= 0:
        return None
    return sum([a * b for a, b in zip(dx, dy)]) / math.sqrt(sxx * syy)


def rets(d, a, b):
    """доходности закрытий соседних минут (только если обе минуты есть) -> {m: ret}; по отсортированному виду серии (TK-441: 3 поиска в словаре на минуту -> один проход)"""
    sq = seq(d, a, b)
    return {m1: v1[3] / v0[3] - 1 for (m0, v0), (m1, v1) in zip(sq, sq[1:]) if m1 == m0 + 1 and v0[3] != 0}


def sub(r, ks, lo, hi):
    """{m: r[m]} для m в (lo, hi]; ks = list(r) по возрастанию (TK-441: вместо прохода по всему словарю на каждую фазу)"""
    return {m: r[m] for m in ks[bisect_right(ks, lo):bisect_right(ks, hi)]}


def pair_corr(r1, r2, lag=0):
    ks = [m for m in r1 if (m + lag) in r2]
    return corr([r1[m] for m in ks], [r2[m + lag] for m in ks]), len(ks)


_r5 = {}


def read_5m(fn, cols):
    """OI/фандинг/ликвидации: [(t_ms, {имя: значение})] по заголовку; файл читается один раз на группу окон инструмента"""
    k = (fn, tuple(cols))
    if k not in _r5:
        _r5[k] = _read_5m(fn, cols)
    return _r5[k]


def _read_5m(fn, cols):
    if not os.path.exists(fn):
        return None
    with gzip.open(fn, "rt") as f:
        h = next(f).rstrip("\n").split(",")
        ix = {c: h.index(c) for c in cols if c in h}
        out = []
        for line in f:
            p = line.rstrip("\n").split(",")
            out.append((int(p[0]), {c: float(p[i]) if i < len(p) and p[i] != "" else None for c, i in ix.items()}))
    return out


_ak = {}


def at_or_before(arr, t):
    """последний элемент arr с ключом <= t; список ключей — один раз на массив (TK-441; arr в значении — id не переиспользуется)"""
    e = _ak.get(id(arr))
    if e is None or e[0] is not arr or e[1] != len(arr):
        e = _ak[id(arr)] = (arr, len(arr), [a[0] for a in arr])
    i = bisect_right(e[2], t) - 1
    return arr[i] if i >= 0 else None


_tc = {}


def trades_day(root, sym, day):
    """Bybit linear: {минута: [buy_usd, sell_usd]} за сутки; кэш «инструмент×сутки» (None — файла нет)"""
    k = (sym, day)
    if k not in _tc:
        fn = "%s/bybit-linear/%s/%s%s.csv.gz" % (root, sym, sym, day)
        out = None
        if os.path.exists(fn):
            out = {}
            with gzip.open(fn, "rt") as f:
                next(f)
                for line in f:
                    p = line.split(",")
                    b = out.setdefault(int(float(p[0]) // 60), [0.0, 0.0])
                    b[0 if p[2] == "Buy" else 1] += float(p[9])
        _tc[k] = out
    return _tc[k]


def trades_min_cube(sym, ma, mb):
    """{минута: [buy_usd, sell_usd]} за [ma, mb] из куба сделок Bybit linear; минуты без сделок не попадают (как в csv-пути).
    None — нет сделок инструмента в сутках окна; {} — сутки со сделками есть (в csv-пути файл суток), а в [ma, mb] их нет"""
    mm, syms, lo = cube_open(CUBE[1], "trades-bybit-linear")
    if sym not in syms:
        return None
    x = mm[syms.index(sym)]
    da, db = max(ma // 1440 * 1440 - lo, 0), min((mb // 1440 + 1) * 1440 - lo, mm.shape[2])
    if da >= db or not ((x[2, da:db] + x[3, da:db]) > 0).any():
        return None
    a, b = max(ma - lo, 0), min(mb - lo + 1, mm.shape[2])
    if a >= b:
        return {}
    x = x[:, a:b]
    ix = ((x[2] + x[3]) > 0).nonzero()[0]
    s = x[:2, ix].tolist()
    return dict(zip((ix + a + lo).tolist(), ([p, q] for p, q in zip(s[0], s[1]))))


def trades_min(root, sym, ma, mb):
    if CUBE[1]:
        return trades_min_cube(sym, ma, mb)
    from datetime import timedelta
    out, found = {}, False
    d = datetime.fromtimestamp(ma * 60, timezone.utc).date()
    d1 = datetime.fromtimestamp(mb * 60, timezone.utc).date()
    while d <= d1:
        t = trades_day(root, sym, d.isoformat())
        if t is not None:
            found = True
            out.update({m: v for m, v in t.items() if ma <= m <= mb})
        d += timedelta(days=1)
    return out if found else None


_oc = {}


def ob_series(ex, mk, sym):
    """минутные срезы стакана (tk414-obmin[-gate]) своего рынка: [(epoch_s, bb, ba, dB25, dA25)], отсортирован; None — нет"""
    if (ex, mk, sym) not in _oc:
        a = []
        for r in OBR.get((ex, mk), ()):
            d = os.path.join(r, sym)
            if os.path.isdir(d):
                for fn in sorted(os.listdir(d)):
                    with open(os.path.join(d, fn), newline="") as f:
                        for line in f:
                            p = line.split(",")
                            if len(p) >= 5:
                                a.append((int(p[0]), float(p[1]), float(p[2]), float(p[3]), float(p[4])))
        a.sort()
        _oc[(ex, mk, sym)] = ([x[0] for x in a], a) if a else None
    return _oc[(ex, mk, sym)]


def agg(x, f):
    x = [v for v in x if v is not None]
    return f(x) if x else None


def features(w, roots, G):
    ex, mk, sym = w["ex"], w["mk"], w["sym"]
    croot, oroot, troot = roots
    F = {}
    d0 = candles(croot, ex, mk, sym)
    ms, mp, me = w["t0"] // 60000, w["tp"] // 60000, w["t1"] // 60000
    if ms not in d0 or mp not in d0 or me not in d0 or not (ms <= mp <= me):
        F["na"] = "нет свечи старта/пика/конца или порядок t_start<=t_peak<=t_end нарушен"
        return F
    mpre = w["tpre"] // 60000 if w.get("tpre") else ms - (me - ms)
    PH = {"pre": (mpre, ms - 1), "leg": (ms, mp), "post": (mp + 1, me)}
    s = w["s"] if w["s"] else (1 if d0[mp][1] >= d0[ms][0] else -1)
    F["sign"] = s
    D = orient(d0, s)
    p0 = abs(d0[ms][0])
    leg = seq(D, ms, mp + 1)
    pull = seq(D, mp + 1, me + 1)
    base = [v[4] for _, v in seq(D, ms - BASE, ms)]
    qmed = med(base)
    F["leg_min"], F["pull_min"], F["hist_min"] = mp - ms, me - mp, len(base)
    have = {"candle": True}
    # возраст: Bybit — launch_ms биржи; для всех — от первой свечи данных (цензурирован началом выгрузки)
    F["data_age_days"] = (ms - dmin(d0)) / 1440
    F["listing_age_days"] = (ms * 60000 - LAUNCH[sym]) / 86400000 if ex == "bybit" and sym in LAUNCH else None
    have["instr"] = F["listing_age_days"] is not None
    pk = max(v[1] for _, v in leg)
    F["leg_bps"] = bps(D[ms][0], pk, p0)
    for ph, (a_, b_) in PH.items():  # база по фазам: ход, размах, оборот к норме
        bs = seq(D, a_, b_ + 1)
        F["mins_" + ph] = len(bs)
        if bs:
            F["ret_bps_" + ph] = bps(bs[0][1][0], bs[-1][1][3], p0)
            F["range_bps_" + ph] = bps(min(v[2] for _, v in bs), max(v[1] for _, v in bs), p0)
            qq = sum(v[4] for _, v in bs)
            F["q_usd_" + ph] = qq
            F["turnover_x_" + ph] = qq / (qmed * len(bs)) if qmed else None
    # --- 12. откат в ноге, premium/discount
    rng = pk - D[ms][0]
    if pull and rng > 0:
        mn = min(v[2] for _, v in pull)
        depth = (pk - mn) / rng
        F["retrace_depth"] = depth
        mnc = min(v[3] for _, v in pull)
        F["retrace_depth_close"] = (pk - mnc) / rng
        for r in G["RET"]:
            F["retrace_hit_%g" % r] = int(depth >= r)
            t = next((m - mp for m, v in pull if (pk - v[2]) / rng >= r), None)
            F["retrace_t_%g" % r] = t
        F["end_pos_in_leg"] = (D[me][3] - D[ms][0]) / rng
    # --- 5,6,9. пулы ликвидности, снятие, возврат телом
    mpk = max(leg, key=lambda x: x[1][1])[0]  # минута максимума ноги
    for H in G["H"]:
        hist = seq(D, ms - H, ms)
        for k in G["K"]:
            fr = [hist[i][1][1] for i in fractals(hist, k, True) if hist[i][0] + k < ms]
            pre = "sw_H%d_k%d" % (H, k)
            F[pre + "_pools"] = len(fr)
            if not fr:
                continue
            top = max(fr)
            F[pre + "_swept_n"] = sum(1 for x in fr if x < pk)
            F[pre + "_top_over_bps"] = bps(top, pk, p0)
            for N in G["N"]:
                for T in G["T"]:
                    if pk > top:
                        F["%s_ret_N%d_T%d" % (pre, N, T)] = ret_close(D, mpk, top, N, T, p0)
        # закрепление телом над пулом (пробой, а не прокол): закрытий выше максимума H в ноге
    for T in G["T"]:
        hist = seq(D, ms - 240, ms)
        if hist:
            top = max(v[1] for _, v in hist)
            F["body_hold_closes_H240_T%d" % T] = sum(1 for _, v in leg if v[3] >= top + T / 1e4 * p0)
    # --- 7. защищённые/незащищённые свинги в ноге
    for k in G["K"]:
        hi_ix, lo_ix = fractals(leg, k, True), fractals(leg, k, False)
        prot = tot = 0
        for i in lo_ix:
            pj = [j for j in hi_ix if j < i]
            if not pj:
                continue
            j = pj[-1]
            pl = [x for x in lo_ix if x < j]
            if not pl:
                continue
            lvl = leg[pl[-1]][1][2]
            tot += 1
            if min(v[2] for _, v in leg[j + 1:i + 1]) < lvl:
                prot += 1
        F["swing_k%d_n" % k] = tot
        F["swing_k%d_unprot_share" % k] = (tot - prot) / tot if tot else None
    runmax, run, dd, hi = 0, 0, 0.0, leg[0][1][1]
    for i in range(1, len(leg)):
        run = run + 1 if leg[i][1][3] > leg[i - 1][1][3] else 0
        runmax = max(runmax, run)
        hi = max(hi, leg[i][1][1])
        dd = max(dd, hi - leg[i][1][2])
    F["leg_max_up_run"] = runmax
    F["leg_max_pullback_bps"] = dd / p0 * 1e4
    F["leg_compression"] = F["leg_bps"] / max(F["leg_max_pullback_bps"], 1.0)
    # --- 8. FVG (бычий в ориентации пампа) и заполнение после пика
    allb = seq(D, ms, me + 1)
    for fm in G["FVGMIN"]:
        gaps = []
        for i in range(1, len(leg) - 1):
            lo, hi2 = leg[i - 1][1][1], leg[i + 1][1][2]
            if hi2 > lo and (hi2 - lo) / p0 * 1e4 >= fm:
                gaps.append((i, lo, hi2))
        pre = "fvg_min%d" % fm
        F[pre + "_n"] = len(gaps)
        F[pre + "_cover_share"] = sum(g[2] - g[1] for g in gaps) / rng if rng > 0 else None
        if gaps and pull:
            for nm, g in (("big", max(gaps, key=lambda g: g[2] - g[1])), ("last", gaps[-1])):
                mnp = min(v[2] for _, v in pull)
                size = g[2] - g[1]
                F["%s_%s_size_bps" % (pre, nm)] = size / p0 * 1e4
                F["%s_%s_edge" % (pre, nm)] = int(mnp <= g[2])
                F["%s_%s_mid" % (pre, nm)] = int(mnp <= (g[1] + g[2]) / 2)
                F["%s_%s_full" % (pre, nm)] = int(mnp <= g[1])
                F["%s_%s_body_cancel" % (pre, nm)] = int(any(v[3] < g[1] for _, v in pull))
    # --- 10. AMD с диапазоном
    for R in G["R"]:
        an, rh, rl = hl(D, ms - R, ms - R // 3)
        if an < R // 3:
            continue
        pre = "amd_R%d" % R
        F[pre + "_range_bps"] = (rh - rl) / p0 * 1e4
        rr = []
        st = R // 3
        for z in range(ms - BASE - R, ms - R, st):
            bn, bh, bl = hl(D, z, z + (R - st))
            if bn >= (R - st) // 2:
                rr.append(bh - bl)
        mr = med(rr)
        F[pre + "_range_vs_base"] = (rh - rl) / mr if mr else None
        for KM in G["KM"]:
            zone = seq(D, ms - R // 3, ms + KM)
            sw = [i for i, (_, v) in enumerate(zone) if v[2] < rl]
            ok = int(any(any(u[3] >= rl for _, u in zone[i:]) for i in sw[:1])) if sw else 0
            F["%s_manip_K%d" % (pre, KM)] = ok
        F[pre + "_break"] = int(pk > rh)
    # --- 9'. старший ТФ: максимум последних D суток до старта (день/неделя), вынос и возврат телом
    for Dd in (1, 7):
        hn, top, _ = hl(D, ms - Dd * 1440, ms)
        F["htf_%dd_hist_min" % Dd] = hn
        if ms - dmin(D) >= Dd * 1440 and hn >= Dd * 1440 * 0.5:  # лукбэк по календарю: первая свеча данных ≤ старт − D сут
            F["htf_%dd_over_bps" % Dd] = bps(top, pk, p0)
            for N in G["N"]:
                for T in G["T"]:
                    if pk > top:
                        F["htf_%dd_ret_N%d_T%d" % (Dd, N, T)] = ret_close(D, mpk, top, N, T, p0)
    # --- 9". дневные фракталы-хаи (k=1) за 7 суток до старта: вынос и возврат телом; недельных нет (истории < 3 нед.)
    dd = {}
    for dy in range((ms - 7 * 1440) // 1440, (ms - 1) // 1440 + 1):  # сутки целиком — из памяти группы, края окна — срезом (TK-441: цикл по 10 080 минутам на окно)
        r = day_ent(D, max(dy * 1440, ms - 7 * 1440), min(dy * 1440 + 1440, ms))
        if r:
            dd[dy] = [r[0], r[1]]
    dk = sorted(k for k in dd if dd[k][1] >= 1000)
    dfr = [dd[k][0] for i, k in enumerate(dk[1:-1], 1) if dk[i - 1] == k - 1 and dk[i + 1] == k + 1 and dd[k][0] > dd[dk[i - 1]][0] and dd[k][0] > dd[dk[i + 1]][0]]
    F["htf_dfr_hist_days"] = len(dk)
    if len(dk) >= 3 and ms - 7 * 1440 >= dmin(D):
        F["htf_dfr_n"] = len(dfr)
        if dfr:
            top = max(dfr)
            F["htf_dfr_over_bps"] = bps(top, pk, p0)
            for N in G["N"]:
                for T in G["T"]:
                    if pk > top:
                        F["htf_dfr_ret_N%d_T%d" % (N, T)] = ret_close(D, mpk, top, N, T, p0)
    # --- 4,12. спот и BTC: раскорреляция, базис, лидерство, SMT
    r_f = rets(D, ms - BASE, me + 1)
    kf = list(r_f)
    kS = kB = []
    spd = candles(croot, ex, "spot", spot_name(ex, sym)) if mk == fut_mkt(ex) else {}
    S = B = None
    r_s = r_b = {}
    F["spot_present"] = int(bool(spd))
    if spd:
        S = orient(spd, s)
        ps = [m for m in range(ms, me + 1) if m in S and m in D]
        if S.get(ms) and D.get(ms):
            F["spot_start_ok"] = 1
            Sp0 = abs(spd[ms][0])
            F["spot_leg_bps"] = bps(S[ms][0], max(v[1] for _, v in seq(S, ms, mp + 1)), Sp0)
            F["spot_minus_fut_leg_bps"] = F["spot_leg_bps"] - F["leg_bps"]
            F["spot_end_bps"] = bps(S[ms][0], S[me][3], Sp0) if me in S else None
        r_s = rets(S, ms - BASE, me + 1)
        kS = list(r_s)
        rw_f = sub(r_f, kf, ms, mp)  # xcorr — только нога
        rw_s = sub(r_s, kS, ms, mp)
        rb_f = sub(r_f, kf, NEG_INF, ms)
        rb_s = sub(r_s, kS, NEG_INF, ms)
        c_w, n_w = pair_corr(rw_f, rw_s)
        c_b, n_b = pair_corr(rb_f, rb_s)
        F["spot_corr_win"], F["spot_corr_base"], F["spot_corr_n"] = c_w, c_b, n_w
        F["spot_decorr"] = (c_b - c_w) if c_w is not None and c_b is not None else None
        for lag in (-3, -1, 1, 3):  # lag>0: спот отстаёт на lag минут от фьючерса
            c, _ = pair_corr(rw_f, rw_s, lag)
            F["spot_xcorr_lag%+d" % lag] = c
        # базис в bps: (фьюч - спот)/спот по закрытию минуты; ориентация знака не нужна, берём исходные цены
        def basis(m):
            return bps(spd[m][3], d0[m][3], abs(spd[m][3])) if m in spd and m in d0 else None
        b0, bp, b1 = basis(ms - 1), basis(mp), basis(me)
        F["basis_pre_bps"], F["basis_peak_bps"], F["basis_end_bps"] = b0, bp, b1
        bb = [bps(spd[m][3], d0[m][3], abs(spd[m][3])) for m in range(ms - BASE, ms) if m in spd and m in d0]  # = basis(m) без None
        bmed = med(bb)
        F["basis_base_med_bps"] = bmed
        F["basis_peak_vs_base_bps"] = (bp - bmed) if bp is not None and bmed is not None else None
        F["basis_peak_minus_pre_bps"] = (bp - b0) if bp is not None and b0 is not None else None
        for H in (60, 240):  # SMT фьюч-спот: кто вынес максимум последних H минут
            hf = seq(D, ms - H, ms)
            hs = seq(S, ms - H, ms)
            if hf and hs:
                f_sw = int(max(v[1] for _, v in seq(D, ms, mp + 1)) > max(v[1] for _, v in hf))
                s_sw = int(max(v[1] for _, v in seq(S, ms, mp + 1)) > max(v[1] for _, v in hs)) if seq(S, ms, mp + 1) else None
                F["smt_fs_H%d_fut_swept" % H], F["smt_fs_H%d_spot_swept" % H] = f_sw, s_sw
                F["smt_fs_H%d_diverge" % H] = int(s_sw is not None and f_sw != s_sw)
    btc = candles(croot, ex, fut_mkt(ex), btc_name(ex)) if sym != btc_name(ex) else {}
    have["spot"], have["btc"] = bool(spd), bool(btc)
    if btc:
        B = orient(btc, s)
        r_b = rets(B, ms - BASE, me + 1)
        kB = list(r_b)
        rw = sub(r_f, kf, ms, me)
        rbb = sub(r_f, kf, NEG_INF, ms)
        cw, _ = pair_corr(rw, sub(r_b, kB, ms, me))
        cb, _ = pair_corr(rbb, sub(r_b, kB, NEG_INF, ms))
        F["btc_corr_win"], F["btc_corr_base"] = cw, cb
        F["btc_decorr"] = (cb - cw) if cw is not None and cb is not None else None
        if ms in B and me in B:
            F["btc_end_bps"] = bps(B[ms][0], B[me][3], abs(btc[ms][0]))
            F["btc_leg_bps"] = bps(B[ms][0], max(v[1] for _, v in seq(B, ms, mp + 1)), abs(btc[ms][0])) if seq(B, ms, mp + 1) else None
        for H in (60, 240):
            hb = seq(B, ms - H, ms)
            lb = seq(B, ms, mp + 1)
            hf = seq(D, ms - H, ms)
            if hb and lb and hf:
                c_sw = int(pk > max(v[1] for _, v in hf))
                b_sw = int(max(v[1] for _, v in lb) > max(v[1] for _, v in hb))
                F["smt_btc_H%d_coin_swept" % H], F["smt_btc_H%d_btc_swept" % H] = c_sw, b_sw
                F["smt_btc_H%d_diverge" % H] = int(c_sw != b_sw)
    oi = fu = tr = None
    dd = tt_ = {}
    # --- OI / фандинг / ликвидности
    if ex == "bybit" and mk == "linear":
        oi = read_5m("%s/bybit/oi/%s.csv.gz" % (oroot, sym), ["open_interest"])
        fu = read_5m("%s/bybit/funding/%s.csv.gz" % (oroot, sym), ["funding_rate"])
    elif ex == "gate":
        oi = read_5m("%s/gate/oi/%s.csv.gz" % (oroot, sym), ["open_interest", "long_liq_usd_new", "short_liq_usd_new"])
        fu = read_5m("%s/gate/funding/%s.csv.gz" % (oroot, sym), ["funding_rate"])
    else:
        oi = fu = None
    if oi:
        col_oi = "open_interest"

        def oi_at(t):
            r = at_or_before(oi, t)
            return r[1][col_oi] if r and t - r[0] <= 900000 and r[1].get(col_oi) else None

        t0, tp, t1 = ms * 60000, mp * 60000, me * 60000
        o_s, o_p, o_e = oi_at(t0), oi_at(tp), oi_at(t1)
        F["oi_leg_pct"] = (o_p / o_s - 1) * 100 if o_s and o_p else None
        F["oi_pull_pct"] = (o_e / o_p - 1) * 100 if o_p and o_e else None
        for R in G["R"]:
            o_b = oi_at(t0 - R * 60000)
            F["oi_pre%d_pct" % R] = (o_s / o_b - 1) * 100 if o_s and o_b else None
        if ex == "gate":
            ts = [a[0] for a in oi]
            for nm, a, b in (("pre180", t0 - 180 * 60000, t0), ("leg", t0, tp + 60000), ("pull", tp + 60000, t1 + 60000)):
                L = sum(r[1].get("long_liq_usd_new") or 0 for r in oi if a <= r[0] < b)
                Sx = sum(r[1].get("short_liq_usd_new") or 0 for r in oi if a <= r[0] < b)
                F["liq_long_usd_" + nm], F["liq_short_usd_" + nm] = L, Sx
            bs = [(r[1].get("long_liq_usd_new") or 0) + (r[1].get("short_liq_usd_new") or 0) for r in oi if t0 - BASE * 60000 <= r[0] < t0]
            F["liq_base_mean_5m_usd"] = sum(bs) / len(bs) if bs else None
    if fu:
        r = at_or_before(fu, ms * 60000)
        F["funding_last"] = r[1]["funding_rate"] if r else None
        b3 = [x[1]["funding_rate"] for x in fu if ms * 60000 - 3 * 86400000 <= x[0] < ms * 60000]
        F["funding_base3d_mean"] = sum(b3) / len(b3) if b3 else None
    # --- 1. поглощение по сделкам (Bybit linear)
    if ex == "bybit" and mk == "linear":
        tr = trades_min(troot, sym, mpre, me)
        F["trades_present"] = int(tr is not None)
        if tr:
            ib, isl = (0, 1) if s > 0 else (1, 0)  # агрессор «по ходу»: buy = v[ib], sel = v[isl]; минуты без сделок — int 0, как раньше
            dd, tt_ = {}, {}
            for m, v in tr.items():
                dd[m], tt_[m] = v[ib] - v[isl], v[ib] + v[isl]
            dl = sum([dd.get(m, 0) for m in range(ms, mp + 1)])
            tl = sum([tt_.get(m, 0) for m in range(ms, mp + 1)])
            F["flow_delta_leg_usd"], F["flow_imb_leg"] = dl, dl / tl if tl else None
            F["flow_delta_per_bps"] = dl / F["leg_bps"] if F["leg_bps"] > 0 else None
            for N in G["ABS_N"]:
                a = [m for m in range(mp - N + 1, mp + 1) if m in tr]
                if not a:
                    continue
                db = sum([dd[m] for m in a])
                tt = sum([tt_[m] for m in a])
                imb = db / tt if tt else 0
                disp = bps(D[a[0]][0], D[a[-1]][3], p0) if a[0] in D and a[-1] in D else None
                F["abs_N%d_imb" % N], F["abs_N%d_disp_bps" % N] = imb, disp
                F["abs_N%d_surge" % N] = tt / (qmed * len(a)) if qmed else None
    # --- фазовый блок (правка 1): признаки источников данных по pre/leg/post; пороги поглощения — шаг 3 (непрерывные колонки)
    have.update({"oi": oi is not None, "fund": fu is not None, "liq": ex == "gate" and oi is not None, "trades": tr is not None})
    ob = ob_series(ex, mk, sym)
    obb = None
    if ob:
        ks, arr = ob
        lo, hi = bisect_left(ks, mpre * 60 - 360 * 60), bisect_left(ks, mpre * 60)
        obb = arr[lo:hi] if hi - lo >= 60 else None
    mo = med([(x[4] if s > 0 else x[3]) for x in obb]) if obb else None
    msp = med([(x[2] - x[1]) / ((x[1] + x[2]) / 2) for x in obb]) if obb and all(x[1] + x[2] > 0 for x in obb) else None
    have["ob"] = bool(ob)
    F["ob_present"] = int(bool(ob))

    def obwin(a_m, b_m):
        if not ob:
            return []
        return arr[bisect_left(ks, a_m * 60):bisect_left(ks, (b_m + 1) * 60)]

    def oi_at2(t):
        r = at_or_before(oi, t) if oi else None
        return r[1].get("open_interest") if r and t - r[0] <= 900000 and r[1].get("open_interest") else None

    for ph, (a_, b_) in PH.items():
        if b_ < a_:
            continue
        rf = sub(r_f, kf, a_, b_)
        if spd and S is not None:
            rs = sub(r_s, kS, a_, b_)
            F["spot_corr_" + ph], _ = pair_corr(rf, rs)
            ss = seq(S, a_, b_ + 1)
            if ss:
                F["spot_ret_bps_" + ph] = bps(ss[0][1][0], ss[-1][1][3], abs(spd[ss[0][0]][0]))
                if F.get("ret_bps_" + ph) is not None:
                    F["spot_gap_bps_" + ph] = F["spot_ret_bps_" + ph] - F["ret_bps_" + ph]
            bsv = [bps(spd[m][3], d0[m][3], abs(spd[m][3])) for m in range(a_, b_ + 1) if m in spd and m in d0]  # = basis2(m) без None
            F["basis_mean_bps_" + ph] = sum(bsv) / len(bsv) if bsv else None
            if bsv and F.get("basis_base_med_bps") is not None:
                F["basis_vs_base_bps_" + ph] = F["basis_mean_bps_" + ph] - F["basis_base_med_bps"]
        if btc and B is not None:
            F["btc_corr_" + ph], _ = pair_corr(rf, sub(r_b, kB, a_, b_))
            bb_ = seq(B, a_, b_ + 1)
            if bb_:
                F["btc_ret_bps_" + ph] = bps(bb_[0][1][0], bb_[-1][1][3], abs(btc[bb_[0][0]][0]))
        if oi:
            o1, o2 = oi_at2(a_ * 60000), oi_at2((b_ + 1) * 60000)
            F["oi_pct_" + ph] = (o2 / o1 - 1) * 100 if o1 and o2 else None
            if ex == "gate":
                rows = [r for r in oi if a_ * 60000 <= r[0] < (b_ + 1) * 60000]
                if rows:
                    F["liq_long_usd_" + ph] = sum(r[1].get("long_liq_usd_new") or 0 for r in rows)
                    F["liq_short_usd_" + ph] = sum(r[1].get("short_liq_usd_new") or 0 for r in rows)
        if tr is not None:
            mm = [m for m in range(a_, b_ + 1) if m in tr]
            if mm:
                dl = sum([dd[m] for m in mm])
                tl = sum([tt_[m] for m in mm])
                F["flow_delta_usd_" + ph], F["flow_imb_" + ph] = dl, (dl / tl if tl else None)
                F["flow_turn_x_" + ph] = tl / (qmed * len(mm)) if qmed else None
        sl = obwin(a_, b_)
        if sl:
            opp = [(x[4] if s > 0 else x[3]) for x in sl]
            F["ob_imb_" + ph] = med([s * (x[3] - x[4]) / (x[3] + x[4]) for x in sl if x[3] + x[4] > 0])
            if mo and mo > 0:
                F["ob_dz_" + ph] = med(opp) / mo
                if opp[0] > 0:
                    F["ob_dz_chg_" + ph] = opp[-1] / opp[0]
            if msp and msp > 0 and all(x[1] + x[2] > 0 for x in sl):
                F["ob_sp_" + ph] = med([(x[2] - x[1]) / ((x[1] + x[2]) / 2) for x in sl]) / msp
    for ph in ("leg", "post"):  # раскорреляция относительно pre той же монеты
        for nm in ("spot", "btc"):
            c0, c1 = F.get("%s_corr_pre" % nm), F.get("%s_corr_%s" % (nm, ph))
            F["%s_decorr_%s" % (nm, ph)] = (c0 - c1) if c0 is not None and c1 is not None else None
    # поглощение по стакану: у пика цена не ушла, стена напротив держится/восполняется (непрерывные: смещение и отношение стены)
    for N in (G["ABS_N"] if ob else ()):  # без стакана (`--ob` не дан / нет среза) — колонок ob_* нет вовсе
        a_ = [m for m in range(mp - N + 1, mp + 1) if m in D]
        sl = obwin(mp - N + 1, mp)
        if a_:
            F["ob_abs_N%d_disp_bps" % N] = bps(D[a_[0]][0], D[a_[-1]][3], p0)
        if len(sl) >= 2:
            o = [(x[4] if s > 0 else x[3]) for x in sl]
            F["ob_abs_N%d_wall_chg" % N] = o[-1] / o[0] if o[0] > 0 else None
    F["_have"] = have
    return F


# --- имена по фазам (правка 1) и покрытие (правка 2)
DROP = re.compile(r"^(flow_delta_leg_usd|oi_leg_pct|oi_pull_pct|liq_(long|short)_usd_(pre180|pull)|spot_corr_(win|base|n)|spot_decorr|"
                  r"btc_corr_(win|base)|btc_decorr|btc_end_bps|btc_leg_bps|spot_end_bps|spot_leg_bps|spot_minus_fut_leg_bps|basis_pre_bps|basis_peak_bps|"
                  r"basis_end_bps|basis_peak_vs_base_bps|basis_peak_minus_pre_bps|end_bps|leg_bps)$")
REN = [  # первое совпадение; "+x" — дописать суффикс, иначе re.sub
    (r"^(htf_\dd_hist_min|htf_dfr_hist_days)$", "+"),  # служебные
    (r"^oi_pre(\d+)_pct$", r"oi_back\1_pct_ctx"),
    (r"^(amd_R\d+_(range|manip)|basis_base_med_bps|funding_|liq_base_mean)", "+_ctx"),
    (r"^(retrace_|end_pos_in_leg|fvg_min\d+_(big|last)_(edge|mid|full|body_cancel))", "+_post"),
    (r"^(sw_|body_hold_|swing_|leg_max_|leg_compression|htf_|amd_R\d+_break|smt_|spot_xcorr|abs_N|ob_abs_N|flow_delta_per_bps|"
     r"fvg_min\d+_(n|cover_share|(big|last)_size_bps))", "+_leg"),
]
SERVICE = re.compile(r"^(sign|leg_min|pull_min|hist_min|spot_present|spot_start_ok|trades_present|ob_present|data_age_days|mins_|htf_\dd_hist_min|htf_dfr_hist_days|_have|na$)")
SRC = [(r"^(spot_|basis_|smt_fs_)", "spot"), (r"^(btc_|smt_btc_)", "btc"), (r"^oi_", "oi"), (r"^funding_", "fund"), (r"^liq_", "liq"),
       (r"^(flow_|abs_N)", "trades"), (r"^(ob_)", "ob"), (r"^listing_age", "instr")]
STRUCT = re.compile(r"^(sw_|fvg_|swing_|htf_|amd_|retrace_|body_hold)")  # пусто при данных = условие признака не выполнено


def rename(F):
    out = {}
    for k, v in F.items():
        if k == "_have" or DROP.match(k):
            continue
        for pat, rp in REN:
            if re.match(pat, k):
                k = k + rp[1:] if rp[0] == "+" else re.sub(pat, rp, k)
                break
        out[k] = v
    return out


def cov_of(k, val, have, hist7):
    if val is not None and val != "":
        return "ok"
    src = next((n for pat, n in SRC if re.match(pat, k)), "candle")
    if not have.get(src, True) or (k.startswith(("htf_7d", "htf_dfr")) and not hist7):
        return "нет_данных"
    return "не_применимо" if STRUCT.match(k) else "нет_данных"


def add_cov(F, have, hist7):
    out = dict(F)
    for k in list(F):
        if not SERVICE.match(k):
            out["cov_" + k] = cov_of(k, F[k], have, hist7)
    out["_cov"] = (have, hist7)
    return out


def work_group(a):
    key, items, roots, G = a
    t = time.time()
    res = []
    if RNG_ON[0]:
        RNG[0] = (min(min(w["tpre"] // 60000 if w["tpre"] else 2 * (w["t0"] // 60000) - w["t1"] // 60000, w["t0"] // 60000) for _, w in items) - 8 * 1440,
                  max(w["t1"] // 60000 for _, w in items) + 3 * 1440)
    for i, w in items:
        try:
            F = features(w, roots, G)
            have = F.get("_have", {})
            hist7 = F.get("data_age_days", 0) >= 7  # календарь: первая свеча данных ≤ старт − 7 сут (не доля минут)
            Fr = add_cov(rename(F), have, hist7)
            sp = int(Fr.get("spot_present") or 0)
            Fr = {k: (v if k == "_cov" else fnum(v)) for k, v in Fr.items()}  # TK-441: формат ячеек в воркере, не в главном
            Fr["_sp"] = sp
            res.append((i, w, Fr))
        except Exception as e:  # окно не должно ронять прогон
            res.append((i, w, {"na": "ошибка: %s" % repr(e)[:150]}))
    for k in [k for k in _cc if k[2] not in ("BTCUSDT", "BTC_USDT")]:
        del _cc[k]
    _tc.clear()
    _oc.clear()
    _or.clear()
    _sq.clear()
    _dy.clear()
    _ak.clear()
    _r5.clear()
    RNG[0] = None
    return res, key, time.time() - t


def main():
    av = sys.argv[1:]
    opt = {"--candles": "/srv/alpha-sda/candles-1m", "--tk415": "/srv/alpha-sda/tk415/out", "--trades": "/data/tk417", "--grid": None, "--procs": "1",
           "--aug-candles": None, "--ob": "", "--instr": None, "--cube": None, "--cube-trades": None, "--cube-from": "", "--cube-range": ""}
    pos = []
    i = 0
    while i < len(av):
        if av[i] in opt:
            opt[av[i]] = av[i + 1]
            i += 2
        else:
            pos.append(av[i])
            i += 1
    inp, outp = pos
    t_all = time.time()
    TT = os.environ.get("TK441_T") == "1"  # TK-441: метки этапов главного процесса в stderr

    def tk(m):
        if TT:
            print("метка %s: %.1f с" % (m, time.time() - t_all), file=sys.stderr)
    G = dict(GRID)
    if opt["--grid"]:
        G.update(json.load(open(opt["--grid"])))
    AUG[0] = opt["--aug-candles"]
    RNG_ON[0] = opt["--cube-range"] == "1"
    CUBE[0], CUBE[1] = opt["--cube"], opt["--cube-trades"]
    if opt["--cube-from"]:
        CUBE_FROM[0] = tms(opt["--cube-from"]) // 60000
    for x in opt["--ob"].split(","):  # биржа/рынок=корень (рынки: futures_usdt, spot, linear)
        if x:
            k, r = x.split("=", 1)
            OBR.setdefault(tuple(k.split("/", 1)), []).append(r)
    if opt["--instr"] and os.path.exists(opt["--instr"]):
        with open(opt["--instr"], encoding="utf-8") as f:
            for line in f:
                p = line.rstrip("\n").split("\t")
                if len(p) >= 3 and p[0] == "linear" and p[2].isdigit():
                    LAUNCH[p[1]] = int(p[2])
    W = []
    with open(inp, newline="", encoding="utf-8") as f:
        rd = csv.DictReader(f)
        h = rd.fieldnames
        c = {"ex": col(h, "биржа", "exchange"), "mk": col(h, "рынок", "market"), "sym": col(h, "инструмент", "монета", "symbol"),
             "t0": col(h, "t_start", "t_start_ms", "начало_utc"), "tp": col(h, "t_peak", "t_peak_ms", "пик_utc"), "t1": col(h, "t_end", "t_end_ms", "конец_utc"),
             "tpre": col(h, "t_pre", "t_pre_ms"), "s": col(h, "знак", "sign", "направление"), "id": col(h, "id", "event_id")}
        for n, r in enumerate(rd):
            s = 0
            if c["s"] and r[c["s"]]:
                s = {"памп": 1, "дамп": -1, "+1": 1, "1": 1, "-1": -1}.get(r[c["s"]].strip().lower(), 0)
            W.append({"id": r[c["id"]] if c["id"] else str(n), "ex": r[c["ex"]], "mk": r[c["mk"]], "sym": r[c["sym"]],
                      "t0": tms(r[c["t0"]]), "tp": tms(r[c["tp"]]), "t1": tms(r[c["t1"]]), "s": s,
                      "tpre": tms(r[c["tpre"]]) if c["tpre"] and r[c["tpre"]] else 0})
    roots = (opt["--candles"], opt["--tk415"], opt["--trades"])
    procs = int(opt["--procs"])
    groups = {}  # окна одного инструмента — в одну задачу: кэш свечей, сделок «инструмент×сутки» и срезов стакана общий
    for i, w in enumerate(W):
        groups.setdefault((w["ex"], w["mk"], w["sym"]), []).append((i, w))
    jobs = [(k, v, roots, G) for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1]))]
    tk("вход прочитан")
    if procs > 1:
        with Pool(procs) as p:
            outs = list(p.imap_unordered(work_group, jobs))
    else:
        outs = [work_group(j) for j in jobs]
    tk("пул кончил")
    res = sorted((r for o in outs for r in o[0]), key=lambda r: r[0])
    for _, key, dt in outs:
        print("группа %s: %.1f с" % ("/".join(key), dt), file=sys.stderr)
    tk("сортировка")
    kd = {}  # порядок первого появления; dict вместо поиска по списку (TK-441: 15 из 35 с серийного хвоста)
    for _, _, F in res:
        for k in F:
            if k not in kd and k != "_cov" and k != "_sp":
                kd[k] = None
    keys = list(kd)
    tk("ключи")
    # признак, которого в окне не вычислялось вовсе (нет источника/условие не выполнено), тоже получает cov_
    cand = [k for k in keys if not k.startswith("cov_") and not SERVICE.match(k)]  # один раз, не на окно
    meta = [("cov_" + k, next((n for pat, n in SRC if re.match(pat, k)), "candle"), k.startswith(("htf_7d", "htf_dfr")), bool(STRUCT.match(k))) for k in cand]
    cov_sig = {}  # cov_of(k, None, have, hist7) зависит только от (have, hist7): таблица один раз на сочетание
    for _, _, F in res:
        if "na" in F:
            continue
        have, hist7 = F.get("_cov", ({}, False))
        sg = (tuple(sorted(have.items())), hist7)
        tb = cov_sig.get(sg)
        if tb is None:
            tb = cov_sig[sg] = [(ck, "нет_данных" if (not have.get(src, True) or (h7 and not hist7)) else ("не_применимо" if st else "нет_данных"))
                                for ck, src, h7, st in meta]
        for ck, v in tb:
            if ck not in F:
                F[ck] = v
    tk("cov")
    base = ["id", "биржа", "рынок", "инструмент", "t_start_ms", "t_peak_ms", "t_end_ms"]
    with open(outp, "w", newline="", encoding="utf-8") as g:
        wr = csv.writer(g)
        wr.writerow(base + keys)
        for _, w, F in res:
            wr.writerow([w["id"], w["ex"], w["mk"], w["sym"], w["t0"], w["tp"], w["t1"]] + [F.get(k, "") for k in keys])
    tk("csv записан")
    # таблица пар фьючерс↔спот и ряд BTC (правка 4)
    pr = {}
    for _, w, F in res:
        k = (w["ex"], w["mk"], w["sym"])
        e = pr.setdefault(k, [0, 0, 0])
        e[0] += 1
        e[1] += F.get("_sp", 0)
        e[2] += int(F.get("cov_btc_corr_leg") == "ok")
    with open(outp + ".pairs.csv", "w", newline="", encoding="utf-8") as g:
        wr = csv.writer(g)
        wr.writerow(["биржа", "рынок", "инструмент", "спот_инструмент", "окон", "окон_со_спотом", "окон_с_рядом_BTC", "ряд_BTC"])
        for (ex, mk, sym), (n, ns, nb) in sorted(pr.items()):
            wr.writerow([ex, mk, sym, spot_name(ex, sym), n, ns, nb, btc_name(ex)])
    na = sum(1 for _, _, F in res if "na" in F)
    print("окон %d, без признаков %d, колонок %d (признаков %d) -> %s; %.1f с, процессов %d" % (
        len(W), na, len(keys), sum(1 for k in keys if not k.startswith("cov_") and not SERVICE.match(k)), outp, time.time() - t_all, procs))


if __name__ == "__main__":
    main()
