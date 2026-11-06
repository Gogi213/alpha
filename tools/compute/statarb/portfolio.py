#!/usr/bin/env python3
"""Портфельный слой П-32 (TK-484): сделки ядра → чистые bps по суткам закрытия, KPI «дней до перехая»,
блочный бутстреп по суткам (блок ⌈n^{1/3}⌉, §10), t-интервал по блокам с df = n_эфф−1 (w3 M-04: тест размера показал, что
перцентильный бутстреп на 15–21 сутках завышает долю ложных у нижней границы 90 %-интервала, см. `block_t_interval`),
плацебо-пары (§9.1). Только stdlib.

Сутки закрытия — сутки минуты `fill_out` сделки (UTC). Значение суток — сумма `net_bps` сделок, закрытых
в эти сутки, и их число (среднее на сделку = сумма / число). Чем нормировать сутки в портфеле — решает
вызывающий; здесь назначаемых чисел нет: число ресэмплов и зерно — обязательные аргументы.
"""
import importlib.util
import math
import os
import random
from collections import defaultdict

MIN_DAY = 1440


def daily_from_trades(trades):
    """trades: [(fill_out_min, net_bps)] (минуты от начала сетки) → {сутки: (сумма_bps, число_сделок)}."""
    acc = defaultdict(lambda: [0.0, 0])
    for m, net in trades:
        a = acc[m // MIN_DAY]
        a[0] += net
        a[1] += 1
    return {d: (a[0], a[1]) for d, a in acc.items()}


def daily_series(daily, first_day, last_day):
    """Плотный ряд сумм bps по суткам [first_day, last_day]; сутки без закрытий = 0."""
    return [daily.get(d, (0.0, 0))[0] for d in range(first_day, last_day + 1)]


def block_len(n):
    """Длина блока ⌈n^{1/3}⌉ (§10)."""
    return max(1, math.ceil(round(n ** (1 / 3), 12)))


def block_bootstrap(series, n_boot, seed, alpha, block=None):
    """Скользящие блоки с оборачиванием; возвращает (среднее, нижняя, верхняя границы, p_нуль).

    `alpha` — двусторонний уровень (границы — квантили alpha/2 и 1−alpha/2); `p_нуль` — двусторонняя
    доля ресэмплов по другую сторону нуля от выборочного среднего (минимум 1/(n_boot+1)).
    """
    n = len(series)
    if n == 0:
        raise ValueError("пустой ряд")
    b = block or block_len(n)
    rng = random.Random(seed)
    nb = math.ceil(n / b)
    means = []
    for _ in range(n_boot):
        s = 0.0
        cnt = 0
        for _ in range(nb):
            st = rng.randrange(n)
            for j in range(b):
                if cnt == n:
                    break
                s += series[(st + j) % n]
                cnt += 1
        means.append(s / n)
    means.sort()
    mean = sum(series) / n
    lo = means[min(n_boot - 1, int(alpha / 2 * n_boot))]
    hi = means[min(n_boot - 1, int((1 - alpha / 2) * n_boot))]
    opp = sum(1 for m in means if (m <= 0 if mean > 0 else m >= 0))
    return mean, lo, hi, min(1.0, 2 * (opp + 1) / (n_boot + 1))


def _betacf(a, b, x):
    """Цепная дробь для неполной беты (метод Лентца)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 400):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-15:
            break
    return h


def _reg_inc_beta(x, a, b):
    """Регуляризованная неполная бета I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    bt = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def t_sf_two_sided(t, df):
    """Двусторонняя вероятность P(|T| ≥ |t|) для t-Стьюдента с df степенями свободы."""
    return _reg_inc_beta(df / (df + t * t), df / 2.0, 0.5)


def t_ppf(p, df):
    """Квантиль t-Стьюдента (0 < p < 1), бисекцией по `t_sf_two_sided`; точность ≈ 1e-12."""
    if not 0.0 < p < 1.0:
        raise ValueError("p вне (0, 1)")
    if p == 0.5:
        return 0.0
    tail = 2.0 * (1.0 - p) if p > 0.5 else 2.0 * p  # P(|T| ≥ |t|) в искомой точке
    lo, hi = 0.0, 1.0
    while t_sf_two_sided(hi, df) > tail:
        hi *= 2.0
        if hi > 1e300:
            raise ValueError("квантиль не найден")
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if t_sf_two_sided(mid, df) > tail:
            lo = mid
        else:
            hi = mid
        if hi - lo <= 1e-13 * max(1.0, hi):
            break
    q = 0.5 * (lo + hi)
    return q if p > 0.5 else -q


_T_CACHE = {}


def _t_ppf_cached(p, df):
    k = (p, df)
    if k not in _T_CACHE:
        _T_CACHE[k] = t_ppf(p, df)
    return _T_CACHE[k]


def block_t_interval(series, alpha, block=None):
    """t-интервал по блокам суток (w3 M-04): возвращает (среднее, нижняя, верхняя границы, p_двусторонний, df).

    Ряд режется подряд на блоки по `block` суток (по умолчанию ⌈n^{1/3}⌉; последний блок может быть короче), так что число
    блоков равно `n_эфф = ⌈n/block⌉` (P-37 §10.2). Дисперсия среднего — по блокам (формула кластерной устойчивости, при равных блоках
    это s²_блоков/n_эфф): V = n_эфф/(n_эфф−1) · Σ_k (S_k − среднее·n_k)² / n², где S_k — сумма блока k, n_k — его длина.
    Границы — среднее ± t_{1−alpha/2, df}·√V при df = n_эфф−1 (`alpha` — двусторонний уровень, как в `block_bootstrap`),
    p — двусторонний по t = среднее/√V. Случайности нет: ни числа ресэмплов, ни зерна.

    Почему не перцентильный бутстреп: тест размера (tools/compute/statarb/size_test.py, 4000 повторов, B = 3000) на синтетическом нуле
    показал у нижней границы 90 %-интервала 8,0–10,3 % вместо 5 % (n = 15 и 21, блок 3, независимые сутки: нормальные, t(3), разреженные);
    t-интервал по блокам — 2,9–5,1 % (консервативно только на 15 разреженных сутках). При AR(1) φ = 0,3 (допущение аудитора) — 6,1–6,8 %
    против 11,4–11,8 % у бутстрепа. Вырожденные случаи: V = 0 →
    интервал схлопывается в среднее (p = 1 при среднем 0, иначе 0); один блок (df = 0) → интервал (−∞, +∞), p = 1.
    """
    n = len(series)
    if n == 0:
        raise ValueError("пустой ряд")
    b = block or block_len(n)
    mean = sum(series) / n
    nb = math.ceil(n / b)
    if nb < 2:
        return mean, -math.inf, math.inf, 1.0, 0
    ss = 0.0
    for k in range(nb):
        seg = series[k * b:(k + 1) * b]
        r = sum(seg) - mean * len(seg)
        ss += r * r
    var = nb / (nb - 1) * ss / (n * n)
    df = nb - 1
    if var <= 0.0:
        return mean, mean, mean, (1.0 if mean == 0.0 else 0.0), df
    se = math.sqrt(var)
    q = _t_ppf_cached(1.0 - alpha / 2.0, df)
    return mean, mean - q * se, mean + q * se, min(1.0, t_sf_two_sided(mean / se, df)), df


def holm(pvals):
    """Поправка Холма: скорректированные p в исходном порядке."""
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    adj = [0.0] * m
    run = 0.0
    for rank, i in enumerate(order):
        run = max(run, min(1.0, (m - rank) * pvals[i]))
        adj[i] = run
    return adj


def _kpi_module():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "archive", "kpi-newhigh.py")
    spec = importlib.util.spec_from_file_location("kpi_newhigh", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def days_to_new_high(series):
    """KPI «дней до перехая» (В-120) по суточной кривой: (периоды закрытые, хвост, число новых максимумов, p90).

    Периоды считает `periods_to_high` из kpi-newhigh.py на событиях «сутки → сумма bps»; хвост — от последнего
    максимума до конца ряда; p90 — по закрытым периодам и хвосту.
    """
    kpi = _kpi_module()
    events = [(i + 1, v) for i, v in enumerate(series) if v != 0.0]
    periods, tail, n_high = kpi.periods_to_high(events, 0, len(series))
    allp = periods + [tail]
    return periods, tail, n_high, kpi.q(allp, 0.9)


def placebo_pairs(symbols, n_pairs, seed, forbidden=()):
    """Случайные пары без повторов из тех же символов (§9.1); `forbidden` — пары отбора (без порядка)."""
    rng = random.Random(seed)
    syms = sorted(symbols)
    bad = {frozenset(p) for p in forbidden}
    out, seen = [], set()
    if len(syms) < 2 or n_pairs > len(syms) * (len(syms) - 1) // 2 - len(bad):
        raise ValueError("пар недостаточно")
    while len(out) < n_pairs:
        a, b = rng.sample(syms, 2)
        key = frozenset((a, b))
        if key in seen or key in bad:
            continue
        seen.add(key)
        out.append((a, b))
    return out
