"""effective_n_from_autocorr (навык alpha-research): оценка больше n — «не определено» (None), не число
(владелец 26.09, В-113). Прежде на 23 сутках П-02 она давала 39,1 и 1047 — и вердикт опирался на 39,1."""
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


def test_anti_persistent_series_is_undefined():
    m = load()
    # чередование знака: автокорреляция на лаге 1 отрицательна — формула дала бы больше n
    r = [1.0 if i % 2 == 0 else -1.0 for i in range(23)]
    r = [x + 0.01 * i for i, x in enumerate(r)]
    assert m.effective_n_from_autocorr(r) is None


def test_positive_autocorrelation_below_n():
    m = load()
    rng = np.random.default_rng(1)
    x = np.cumsum(rng.normal(size=200)) * 0.1 + rng.normal(size=200) * 0.01  # сильная положительная автокорреляция
    v = m.effective_n_from_autocorr(x)
    assert v is not None and 1.0 <= v < 200


def test_never_above_n_on_short_series():
    m = load()
    rng = np.random.default_rng(20260926)
    undefined = 0
    for _ in range(2000):
        v = m.effective_n_from_autocorr(rng.normal(size=23))
        if v is None:
            undefined += 1
        else:
            assert 1.0 <= v <= 23
    assert undefined > 0  # на белом шуме из 23 точек отрицательная сумма автокорреляций встречается часто


def test_short_and_constant_series_unchanged():
    m = load()
    assert m.effective_n_from_autocorr([1.0, 2.0, 3.0]) == 3.0  # n < 8 — как было
    assert m.effective_n_from_autocorr([5.0] * 20) == 20.0  # нет разброса — как было
