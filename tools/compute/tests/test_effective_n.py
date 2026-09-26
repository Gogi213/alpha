"""effective_n_from_autocorr (навык alpha-research): оценка не больше n — потолок n (Судья 26.09, b8f2988).
Прежде на 23 сутках П-02 она давала 39,1 и 1047; «не определено» вместо числа (aed6288) отозвано: на независимых
данных оно срабатывало в 63–80 % случаев и чаще пускало к вердикту зависимые ряды."""
import importlib.util
import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "..", "..", "..", ".claude", "skills", "alpha-research", "scripts", "effective_n.py")


def load():
    spec = importlib.util.spec_from_file_location("effective_n", PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def ar1(rng, n, phi):
    x = np.empty(n)
    x[0] = rng.normal()
    for i in range(1, n):
        x[i] = phi * x[i - 1] + rng.normal()
    return x


def test_white_noise_short_capped_at_n():
    m = load()
    rng = np.random.default_rng(20260926)
    vals = [m.effective_n_from_autocorr(rng.normal(size=23)) for _ in range(2000)]
    assert all(v is not None and 1.0 <= v <= 23 for v in vals)
    assert sum(v == 23 for v in vals) > len(vals) / 2  # сумма ρ_k короткого ряда чаще отрицательна — потолок


def test_anti_persistent_series_is_n():
    m = load()
    r = [(1.0 if i % 2 == 0 else -1.0) + 0.01 * i for i in range(23)]
    assert m.effective_n_from_autocorr(r) == 23.0


def test_white_noise_n40_mostly_passes_30():
    m = load()
    rng = np.random.default_rng(40)
    vals = [m.effective_n_from_autocorr(rng.normal(size=40)) for _ in range(2000)]
    assert sum(v is not None and v >= 30 for v in vals) / len(vals) >= 0.85


def test_positive_dependence_below_n():
    m = load()
    rng = np.random.default_rng(1)
    vals = [m.effective_n_from_autocorr(ar1(rng, 290, 0.5)) for _ in range(200)]
    assert float(np.median(vals)) < 290


def test_short_and_constant_series_unchanged():
    m = load()
    assert m.effective_n_from_autocorr([1.0, 2.0, 3.0]) == 3.0  # n < 8 — как было
    assert m.effective_n_from_autocorr([5.0] * 20) == 20.0  # нет разброса — как было
