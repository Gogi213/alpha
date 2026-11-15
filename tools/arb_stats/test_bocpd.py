"""Тесты bocpd.py: сверка с перебором всех разбиений и обнаружение смены режима на синтетике."""
import itertools

import numpy as np
from scipy.special import logsumexp

import bocpd


def brute_logev(x, H):
    """Сумма по всем разбиениям шагов 1..n (граница после шага t — вероятность H) от произведения правдоподобий сегментов."""
    n = len(x) - 1
    tot = []
    for cuts in itertools.product([0, 1], repeat=n - 1):
        b = [0] + [t + 1 for t, c in enumerate(cuts) if c] + [n]
        lp = sum(np.log(H) if c else np.log1p(-H) for c in cuts)
        lp += sum(bocpd.segment_logev(x, i, j) for i, j in zip(b[:-1], b[1:]))
        tot.append(lp)
    return logsumexp(tot)


def test_matches_enumeration():
    rng = np.random.default_rng(1)
    x = np.cumsum(rng.normal(0, 1, 9)) * 0.3
    for H in (0.05, 0.3):
        _, total, _ = bocpd.bocpd_ar1(x, H)
        assert abs(total - brute_logev(x, H)) < 1e-9


def test_detects_shift():
    rng = np.random.default_rng(2)
    x = np.zeros(600)
    for t in range(1, 600):
        x[t] = 0.8 * x[t - 1] + rng.normal(0, 0.3) + (0.0 if t < 300 else 0.6)   # на 300-м шаге среднее уходит с 0 на 3
    rl, _, _ = bocpd.bocpd_ar1(x, 1 / 200)
    assert rl[290] > 200 and rl[299 + 40] < 60
    st = bocpd.regimes_from_rl(rl)
    assert any(abs(s - 300) < 30 for s in st)


def test_exp_matches_enumeration():
    rng = np.random.default_rng(5)
    tau = np.r_[rng.exponential(60, 4), rng.exponential(3000, 4)]
    for H in (0.05, 0.3):
        _, total = bocpd.bocpd_exp(tau, H)
        n = len(tau)
        tot = []
        for cuts in itertools.product([0, 1], repeat=n - 1):
            b = [0] + [t + 1 for t, c in enumerate(cuts) if c] + [n]
            lp = sum(np.log(H) if c else np.log1p(-H) for c in cuts)
            lp += sum(bocpd.segment_logev_exp(tau, i, j) for i, j in zip(b[:-1], b[1:]))
            tot.append(lp)
        assert abs(total - logsumexp(tot)) < 1e-9
