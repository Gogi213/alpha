"""Байесовский онлайн-поиск смен режима (Adams–MacKay) для ряда с авторегрессией: внутри режима x_t = c + φ·x_{t−1} + ε, ε ~ N(0, s²).

Сопряжённая модель Normal–Inverse-Gamma на (c, φ, s²); предсказание — t-Стьюдента; постоянная вероятность смены режима H на шаг.
Причинный (фильтр): режим в момент t определяется только данными до t. Зачем он, а не PELT: PELT — оптимизация по всему ряду целиком
(назад смотрит и вперёд), «режим на момент t» из него — подглядывание; BOCPD на каждом шаге отдаёт распределение длины текущего режима r_t
по данным до t — именно «возраст режима» для живой таблицы и честной проверки вне выборки. Авторегрессия — потому что середина пары
автокоррелирована (ρ за 1 мин 0,5…1): без неё независимая модель «видит» смену среднего в каждом медленном колебании.
"""
import numpy as np
from scipy.special import gammaln


def logsumexp(a):
    m = a.max()
    return m + np.log(np.exp(a - m).sum())


PRIOR = {"b0": (0.0, 0.7), "lam0": (0.1, 2.0), "a0": 1.0, "s0": 0.3}   # среднее (c, φ), точность (c, φ), IG(a0, s0)


def _post(st, u, prior):
    """Параметры апостериорного предсказания по статистикам гипотез. -> (log p(y | …) нужны loc, scale², ν)."""
    n, su, suu, sy, suy, syy = st
    l1, l2 = prior["lam0"]
    c0, p0 = prior["b0"]
    a11, a12, a22 = l1 + n, su, l2 + suu
    r1, r2 = l1 * c0 + sy, l2 * p0 + suy
    det = a11 * a22 - a12 * a12
    be1 = (a22 * r1 - a12 * r2) / det
    be2 = (-a12 * r1 + a11 * r2) / det
    quad = r1 * be1 + r2 * be2
    a = prior["a0"] + n / 2
    b = prior["s0"] + 0.5 * (syy + l1 * c0 * c0 + l2 * p0 * p0 - quad)
    q = (a22 - 2 * u * a12 + u * u * a11) / det
    return be1 + be2 * u, b / a * (1 + q), 2 * a


def _logt(y, loc, sc2, nu):
    z = (y - loc) ** 2 / (nu * sc2)
    return gammaln((nu + 1) / 2) - gammaln(nu / 2) - 0.5 * np.log(nu * np.pi * sc2) - (nu + 1) / 2 * np.log1p(z)


PRUNE = 30.0   # гипотезы с вероятностью меньше e^−30 от наибольшей отбрасываются


def bocpd_ar1(x, hazard, rmax=1440, prior=PRIOR):
    """x — ряд без NaN (нормированный). Шаги t = 1..n−1: регрессор u = x[t−1], наблюдение y = x[t].
    -> (r: наиболее вероятная длина режима после каждого шага (uint16, длина n−1), логарифм правдоподобия данных, P(r ≤ 5) после каждого шага).
    Список гипотез — только заметные (отбрасываются с вероятностью < e^−30 от наибольшей); rmax — предел длины."""
    x = np.asarray(x, float)
    n = len(x) - 1
    rl = np.zeros(n, np.uint16)
    pcp = np.zeros(n)
    lh, l1h = np.log(hazard), np.log1p(-hazard)
    a0 = prior["a0"]
    g0 = gammaln(a0 + np.arange(n + 2) / 2)          # gammaln(ν/2), ν = 2·a, a = a0 + n/2
    g1 = gammaln(a0 + np.arange(n + 2) / 2 + 0.5)
    rr = np.zeros(1, np.int64)
    lp = np.zeros(1)
    st = np.zeros((6, 1))
    total = 0.0
    for t in range(n):
        u, y = x[t], x[t + 1]
        loc, sc2, nu = _post(st, u, prior)
        z = (y - loc) ** 2 / (nu * sc2)
        lpi = g1[rr] - g0[rr] - 0.5 * np.log(nu * np.pi * sc2) - (nu + 1) / 2 * np.log1p(z)
        joint = lp + lpi
        ev = logsumexp(joint)
        total += ev
        lp = np.concatenate([[ev + lh], joint + l1h])
        lp -= logsumexp(lp)
        st = np.concatenate([np.zeros((6, 1)), st + np.array([1.0, u, u * u, y, u * y, y * y])[:, None]], axis=1)
        rr = np.concatenate([[0], rr + 1])
        keep = (lp > lp.max() - PRUNE) & (rr <= rmax)
        if not keep.all():
            lp, st, rr = lp[keep], st[:, keep], rr[keep]
        k = int(np.argmax(lp))
        rl[t] = rr[k]
        pcp[t] = float(np.exp(logsumexp(lp[rr <= 5]))) if (rr <= 5).any() else 0.0
    return rl, total, pcp


def segment_logev(x, i, j, prior=PRIOR):
    """log p(y_{i+1..j} | u) одного сегмента по формуле сопряжённой модели (для проверки)."""
    u, y = np.asarray(x[i:j], float), np.asarray(x[i + 1:j + 1], float)
    n = len(y)
    l1, l2 = prior["lam0"]
    c0, p0 = prior["b0"]
    L = np.array([[l1 + n, u.sum()], [u.sum(), l2 + (u * u).sum()]])
    rhs = np.array([l1 * c0 + y.sum(), l2 * p0 + (u * y).sum()])
    be = np.linalg.solve(L, rhs)
    a0 = prior["a0"]
    an = a0 + n / 2
    bn = prior["s0"] + 0.5 * ((y * y).sum() + l1 * c0 * c0 + l2 * p0 * p0 - rhs @ be)
    return (gammaln(an) - gammaln(a0) + 0.5 * (np.log(l1 * l2) - np.log(np.linalg.det(L))) + a0 * np.log(prior["s0"]) - an * np.log(bn) - n / 2 * np.log(2 * np.pi))


def regimes_from_rl(rl, min_drop=5, merge=10):
    """Оценённые начала режимов: шаг, где длина режима упала (r_t < r_{t−1} − min_drop + 1), начало = t − r_t. Соседние начала ближе merge шагов сливаются.
    -> индексы начал (в шагах x), возрастающие; первый — 0."""
    r = rl.astype(np.int64)
    drop = np.flatnonzero(r[1:] < r[:-1] - (min_drop - 1)) + 1
    starts = [0]
    for t in drop:
        s = int(t - r[t])
        if s - starts[-1] >= merge:
            starts.append(s)
    return np.array(starts)


def bocpd_exp(tau, hazard, a0=1.0, b0=1800.0):
    """BOCPD для интервалов между качелями τ_i (с): τ ~ Exp(λ), λ ~ Gamma(a0, b0), постоянная вероятность смены H на интервал.
    -> (r: наиболее вероятное число интервалов текущего режима после каждого интервала, логарифм правдоподобия)."""
    tau = np.asarray(tau, float)
    n = len(tau)
    rl = np.zeros(n, np.int32)
    lh, l1h = np.log(hazard), np.log1p(-hazard)
    rr = np.zeros(1, np.int64)
    lp = np.zeros(1)
    al = np.array([a0])
    be = np.array([b0])
    total = 0.0
    for t in range(n):
        lpi = np.log(al) + al * np.log(be) - (al + 1) * np.log(be + tau[t])
        joint = lp + lpi
        ev = logsumexp(joint)
        total += ev
        lp = np.concatenate([[ev + lh], joint + l1h])
        lp -= logsumexp(lp)
        al = np.concatenate([[a0], al + 1])
        be = np.concatenate([[b0], be + tau[t]])
        rr = np.concatenate([[0], rr + 1])
        keep = lp > lp.max() - PRUNE
        if not keep.all():
            lp, al, be, rr = lp[keep], al[keep], be[keep], rr[keep]
        rl[t] = rr[int(np.argmax(lp))]
    return rl, total


def segment_logev_exp(tau, i, j, a0=1.0, b0=1800.0):
    """log p(τ_i..τ_{j−1}) одного сегмента (для проверки)."""
    t = np.asarray(tau[i:j], float)
    n = len(t)
    return float(gammaln(a0 + n) - gammaln(a0) + a0 * np.log(b0) - (a0 + n) * np.log(b0 + t.sum()))
