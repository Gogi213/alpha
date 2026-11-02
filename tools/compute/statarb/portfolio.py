#!/usr/bin/env python3
"""Портфельный слой П-32 (TK-484): сделки ядра → чистые bps по суткам закрытия, KPI «дней до перехая»,
блочный бутстреп по суткам (блок ⌈n^{1/3}⌉, §10), плацебо-пары (§9.1). Только stdlib.

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
