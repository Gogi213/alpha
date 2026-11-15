"""Тесты swings_fit.py: формула частоты качелей OU против имитации."""
import numpy as np

import swings_fit as sf


def simulate_rate(theta, U, Lw, n=3_000_000, seed=0):
    rng = np.random.default_rng(seed)
    phi = np.exp(-theta)
    e = rng.normal(0, np.sqrt(1 - phi * phi), n)
    x = np.empty(n)
    x[0] = 0.0
    for t in range(1, n):
        x[t] = phi * x[t - 1] + e[t]
    side, cnt = 0, 0
    for v in x:
        s = 1 if v >= U else (-1 if v <= Lw else 0)
        if s != 0 and s != side:
            cnt += side != 0
            side = s
    return cnt / (n / 3600.0)


def test_ou_rate_matches_simulation():
    theta = np.log(2) / 300.0       # полураспад 5 мин
    for U, Lw in ((0.8, -0.8), (1.2, -0.3)):
        pred = sf.ou_swing_rate(0.0, 1.0, theta, U, Lw)
        sim = simulate_rate(theta, U, Lw)
        assert abs(pred - sim) / pred < 0.12, (U, Lw, pred, sim)


def test_far_barrier_zero():
    assert sf.ou_swing_rate(0.0, 0.01, 1e-3, 0.5, -0.5) == 0.0
