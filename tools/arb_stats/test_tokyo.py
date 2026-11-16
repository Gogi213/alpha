"""Тесты tokyo_run: жизнь расхождения, отсечка устаревания 60 с, круги по задержке. Запуск: python3 -m pytest test_tokyo.py (на srv)."""
import numpy as np

import core
import cross
import outlier
import tokyo_run as tr

T0 = 1_800_000_000_000


def book(e, bid, ask):
    n = len(e)
    return {"e": np.array(e, np.int64), "rx": np.array(e, np.int64) + 100, "lag": np.full(n, 100, np.int32), "bid": np.array(bid, float), "ask": np.array(ask, float),
            "dbid": np.full(n, 1000.0), "dask": np.full(n, 1000.0)}


def pair(spike_len_ms=40, gap=None):
    """Два часа: B стоит 100.00/100.02, события каждую секунду; A то же, но на 40 мс бид уходит на 100.30 (Sell ≈ 28 bps) раз в 10 мин. gap — (с, с) тишина A."""
    es = [T0 + 1000 * k for k in range(7200)]
    if gap:
        es = [e for e in es if not (T0 + gap[0] * 1000 <= e < T0 + gap[1] * 1000)]
    eb = [T0 + 1000 * k + 500 for k in range(7200)]
    ea, ba, aa = [], [], []
    for e in es:
        ea.append(e)
        ba.append(100.0)
        aa.append(100.02)
        k = (e - T0) // 1000
        if k % 600 == 300 and k >= 3600:
            ea.append(e + 100)
            ba.append(100.30)
            aa.append(100.32)
            ea.append(e + 100 + spike_len_ms)
            ba.append(100.0)
            aa.append(100.02)
    A = book(ea, ba, aa)
    B = book(eb, [100.0] * 7200, [100.02] * 7200)
    return A, B


def test_life_and_cap():
    tr.G.update({"thetas": (20.0,)})
    A, B = pair()
    t1 = T0 + 7200 * 1000
    r = tr.life_stats(A, B, T0 + 3600 * 1000, t1)
    h = r["th"][20.0]
    assert h["n"] == 6 and h["cens"] == 0
    assert abs(h["q"][1] - 40) <= 1, h["q"]
    assert h["surv"][tr.LATS.index(20)] == 1.0 and h["surv"][tr.LATS.index(50)] == 0.0
    assert r["valid_share"] > 0.99


def test_cap_drops_stale_rows():
    A, B = pair(gap=(100, 190))   # A молчит 90 с → ряд пары вне границы 60 с
    t1 = T0 + 7200 * 1000
    T, S, Bu, ia, ib = tr.pair_series_cap(A, B, t1)
    T0_, *_ = tr._pair_series(A, B, t1)
    assert len(T) < len(T0_)
    assert (T - A["e"][ia] <= tr.CAP_MS).all() and (T - B["e"][ib] <= tr.CAP_MS).all()
    dropped = np.setdiff1d(T0_, T)
    assert dropped.min() >= T0 + 159_000 and dropped.max() < T0 + 191_000


def test_taker_delay_kills_spike():
    A, B = pair(spike_len_ms=40)
    t0, t1 = T0 + 3600 * 1000, T0 + 7200 * 1000
    P = outlier.prep_tt(A, B, t0, t1)
    ent = outlier.entries_for(P, 20.0)
    a0, _ = outlier.simulate_tt(P, ent, 0, "full")
    a50, _ = outlier.simulate_tt(P, ent, 50, "full")
    assert len(a0) and np.nanmedian(a0[:, 2]) > 20            # L = 0: вход по ценам выброса
    assert np.nanmedian(a50[:, 2]) < 5                          # L = 50 мс > жизни выброса 40 мс: цены уже вернулись
    x0, _ = cross.simulate_cross(P, ent, 0, 20.0)
    assert x0.shape[1] == cross.NC
