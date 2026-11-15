"""Тесты swings_oos2.py: причинность предикторов, AAFT-плацебо, Спирмен по популяции."""
import numpy as np

import swings_oos2 as so

HOUR = so.HOUR


def test_aaft_keeps_count_values():
    rng = np.random.default_rng(1)
    c = rng.poisson(3, 56)
    c2 = so.aaft_counts(c, rng)
    assert sorted(c2) == sorted(c)
    assert not np.array_equal(c2, c)


def test_surrogate_events_counts_and_tail():
    rng = np.random.default_rng(2)
    tc = np.sort(rng.integers(0, 60 * HOUR, 400))
    s = so.surrogate_events(tc, 56, rng)
    assert len(s) == len(tc)
    assert np.array_equal(s[s >= 56 * HOUR], tc[tc >= 56 * HOUR])
    assert sorted(np.bincount(s[s < 56 * HOUR] // HOUR, minlength=56)) == sorted(np.bincount(tc[tc < 56 * HOUR] // HOUR, minlength=56))


def test_series_preds_causal_and_outcome():
    rng = np.random.default_rng(3)
    tc = np.sort(rng.integers(0, 40 * HOUR, 300))
    to = np.arange(8 * HOUR, 30 * HOUR, so.STEP)
    end = 40 * HOUR
    full, y, k = so.series_preds(tc, 0, to, end, 50, (3.0, 0.2))
    for i in (0, 10, 25):
        cut = tc[tc <= to[i]]
        p, _, k2 = so.series_preds(cut, 0, to[i:i + 1], end, 50, (3.0, 0.2))
        assert np.allclose(full[:, i], p[:, 0], rtol=1e-5), (i, full[:, i], p[:, 0])
        assert k[i] == k2[0]
    for ih, h in enumerate(so.HS):
        i = 5
        n = ((tc > to[i]) & (tc <= to[i] + h * HOUR)).sum()
        assert y[ih, i] == n
    assert np.isnan(y[-1, np.flatnonzero(to + 24 * HOUR > end)[0]])


def test_rho_mean_population_and_shuffle():
    rng = np.random.default_rng(4)
    x = rng.random((200, 3)).astype(np.float32)
    z = x * 2 + 1
    m = np.ones((200, 3), bool)
    assert abs(so.rho_mean(x, z, m)[0] - 1) < 1e-9
    assert abs(so.rho_mean(x, z, m, rng)[0]) < 0.25
    m[:, 1:] = False
    r, n, first = so.rho_mean(x, z, m)
    assert n == 1 and first == 200
    m[:20, 0] = False
    m[170:, 0] = False
    assert so.rho_mean(x, z, m)[1] == 1
    m[:160, 0] = False
    assert np.isnan(so.rho_mean(x, z, m)[0])
