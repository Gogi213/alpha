"""Офлайн-разбор признаков basis_split (pkl из basis_split_run.py): минутные ряды, лестница масштабов, спектр, ACF, волна по суткам, фандинг, времена жизни.

Все функции работают на малом (минутные таблицы, гистограммы) — без возврата к сырью. Единицы — %, время — мин / мс, сутки — GMT+4.
"""
import numpy as np

import basis_split as bs

LADDER_W = (2, 5, 10, 20, 40, 60, 80, 120, 160, 240, 320, 480, 640)   # окна лестницы (мин): компонента k = медиана(W_k) − медиана(W_{k+1})
ACF_LAGS = (1, 2, 5, 10, 20, 30, 60, 90, 120, 180, 240, 360, 480, 720)
BAND_EDGES = (2, 5, 10, 20, 40, 80, 160, 320, 640, 1280, 4000)   # периоды спектра, мин


def group_of(key):
    return "bx" if "bingx" in key[1:] else "ok"


def day_index_of_minutes(f, n_min=None):
    return f["day_of_min"].astype(int)


def fill_nan(x):
    """Линейная интерполяция NaN; крайние NaN — ближайшим значением. -> (ряд, доля NaN)."""
    x = np.asarray(x, float)
    bad = np.isnan(x)
    if bad.all():
        return None, 1.0
    if not bad.any():
        return x, 0.0
    i = np.arange(len(x))
    return np.interp(i, i[~bad], x[~bad]), float(bad.mean())


def ladder(f):
    """Лестница масштабов по минутным медианам середины. -> (границы-оценки: список имён, std по компонентам, p95−p5 по компонентам)
    компоненты: [raw−w2, w2−w5, w5−w10, …, w640−день, день−уровень окна]; уровень окна = медиана всего ряда."""
    x = f["mins"]["med"].astype(float)
    refs = [x] + [bs.rolling_med_centred(x, w) for w in LADDER_W] + [f["ref_day"][f["day_of_min"].astype(int)], np.full(len(x), f["level_all"] if f["level_all"] is not None else np.nan)]
    comps = [refs[i] - refs[i + 1] for i in range(len(refs) - 1)]
    std = np.array([np.nanstd(c) for c in comps])
    rng = np.array([np.nanpercentile(c, 95) - np.nanpercentile(c, 5) if np.isfinite(c).any() else np.nan for c in comps])
    return std, rng


def ladder_names():
    w = ("1",) + tuple(str(v) for v in LADDER_W)
    return [f"{w[i]}→{w[i + 1]} мин" for i in range(len(w) - 1)] + [f"{w[-1]} мин→сутки", "сутки→окно"]


def acf(xt, lags):
    """Автокорреляция ряда (уже без уровня суток), по FFT; значения на lags (мин). None — ряд пуст."""
    x, _ = fill_nan(xt)
    if x is None:
        return None
    x = x - x.mean()
    n = len(x)
    v = (x * x).sum()
    if v <= 0:
        return None
    F = np.fft.rfft(x, 2 * n)
    a = np.fft.irfft(F * np.conj(F))[:n] / v
    return np.array([a[L] / (1 - L / n) if L < n else np.nan for L in lags])      # поправка на конечную длину


def spectrum(xt):
    """Периодограмма (окно Хэнна) минутного ряда: -> (периоды мин, мощность) без нулевой частоты. None — ряд пуст."""
    x, fr = fill_nan(xt)
    if x is None:
        return None
    x = x - x.mean()
    n = len(x)
    w = np.hanning(n)
    P = np.abs(np.fft.rfft(x * w)) ** 2 / (w * w).sum()
    fq = np.fft.rfftfreq(n, 1.0)                       # циклов в минуту
    return 1.0 / fq[1:], P[1:]


def band_shares(per, P):
    """Доли дисперсии по полосам периодов BAND_EDGES (мин)."""
    out = []
    tot = P.sum()
    for a, b in zip(BAND_EDGES[:-1], BAND_EDGES[1:]):
        m = (per >= a) & (per < b)
        out.append(float(P[m].sum() / tot) if tot > 0 else np.nan)
    return np.array(out)


def peak_period(per, P, lo=60, hi=1000):
    """Самый сильный пик в диапазоне периодов [lo, hi) мин и его «выраженность» = мощность / медиана мощности в диапазоне [lo/2, 2·hi)."""
    m = (per >= lo) & (per < hi)
    if m.sum() < 3:
        return None
    ref = np.median(P[(per >= lo / 2) & (per < 2 * hi)])
    i = np.flatnonzero(m)[np.argmax(P[m])]
    return float(per[i]), float(P[i] / ref) if ref > 0 else None


def smooth(h, k=1):
    """Скользящее среднее по 2k+1 корзинам (края — как есть)."""
    h = np.asarray(h, float)
    pad = np.r_[np.full(k, h[0]), h, np.full(k, h[-1])]
    return np.convolve(pad, np.ones(2 * k + 1) / (2 * k + 1), "valid")


def modes_valleys(h, k=1, prom=0.15):
    """Моды и впадины сглаженной плотности h: пик — локальный максимум ≥ prom·max(h); впадина — минимум между соседними пиками.
    -> (индексы пиков, индексы впадин, глубина впадины = мин / min(соседние пики))."""
    s = smooth(h, k)
    if s.max() <= 0:
        return [], [], []
    pk = [i for i in range(1, len(s) - 1) if s[i] >= s[i - 1] and s[i] > s[i + 1] and s[i] >= prom * s.max()]
    pk = [i for i in pk]
    vl, dp = [], []
    for a, b in zip(pk[:-1], pk[1:]):
        j = a + int(np.argmin(s[a:b + 1]))
        vl.append(j)
        dp.append(float(s[j] / min(s[a], s[b])) if min(s[a], s[b]) > 0 else 0.0)
    return pk, vl, dp


def lt_label(b):
    """Подпись корзины b (0 … NB−1): интервал мс."""
    lo = 0.0 if b == 0 else bs.LT_EDGES[b - 1]
    hi = bs.LT_EDGES[b] if b < len(bs.LT_EDGES) else np.inf
    return lo, hi


def fmt_ms(v):
    if v == np.inf:
        return "∞"
    if v < 1000:
        return f"{v:.0f} мс"
    if v < 60_000:
        return f"{v / 1000:.3g} с"
    if v < 3600_000:
        return f"{v / 60_000:.3g} мин"
    return f"{v / 3600_000:.3g} ч"


def line_stats(xt, t0_ms, period_min, t_ref_ms):
    """Линия спектра на периоде period_min (мин): амплитуда, фаза (рад) относительно t_ref_ms и «выраженность» = амплитуда² / медиана амплитуд² на соседних
    периодах (±10…40 %, без самой линии). Ряд — минутный (без уровня суток); NaN заполняются интерполяцией."""
    x, fr = fill_nan(xt)
    if x is None or len(x) < 3 * period_min:
        return None
    x = x - x.mean()
    k = np.arange(len(x))
    tm = t0_ms / 60000 + k - t_ref_ms / 60000

    def amp(p):
        c = (x * np.exp(-2j * np.pi * tm / p)).sum() * 2 / len(x)
        return c

    c = amp(period_min)
    nb = [abs(amp(period_min * m)) ** 2 for m in (0.6, 0.7, 0.8, 0.9, 1.1, 1.25, 1.4)]
    med = float(np.median(nb))
    return {"amp": float(abs(c)), "phase": float(np.angle(c)), "snr": float(abs(c) ** 2 / med) if med > 0 else None}


def rayleigh(angles):
    """Концентрация углов (радианы): средняя длина вектора R и p-значение критерия Рэлея (приближение)."""
    a = np.asarray(angles, float)
    n = len(a)
    if n < 5:
        return None, None
    R = float(np.hypot(np.cos(a).mean(), np.sin(a).mean()))
    z = n * R * R
    p = float(np.exp(np.sqrt(1 + 4 * n + 4 * (n * n - (n * R) ** 2)) - (1 + 2 * n)))
    return R, min(1.0, p)


def hour_profile(xt, t0_ms, day_ms_edges):
    """Средний xt по часам суток GMT+4 (24 значения; NaN — нет данных) и по суткам: -> (nday, 24)."""
    n = len(xt)
    tm = t0_ms + np.arange(n) * 60000
    hod = ((tm + bs.TZ_MS) // 3600_000 % 24).astype(int)
    dix = np.searchsorted(np.array(day_ms_edges[1:-1], np.int64), tm, "right")
    nd = len(day_ms_edges) - 1
    out = np.full((nd, 24), np.nan)
    for d in range(nd):
        for h in range(24):
            m = (dix == d) & (hod == h)
            if m.sum() >= 30 and np.isfinite(xt[m]).sum() >= 30:
                out[d, h] = np.nanmean(xt[m])
    return out
