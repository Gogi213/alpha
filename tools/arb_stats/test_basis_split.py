import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import basis_split as bs  # noqa: E402

T0 = 1_794_600_000_000 // 60000 * 60000
H = 3600_000


def books(n_s, level, wave_amp, wave_h, outliers):
    """A: середина 100·(1 + уровень + волна + выбросы), спред 0,02; B стоит 99,99/100,01. Шаг 1 с."""
    t = T0 + np.arange(n_s, dtype=np.int64) * 1000
    pr = 1.0 + level / 100 + wave_amp / 100 * np.sin(2 * np.pi * (t - T0) / (wave_h * H))
    for s, dur, amp in outliers:
        pr[(t >= T0 + s) & (t < T0 + s + dur)] += amp / 100
    mid = 100 * pr
    A = {"e": t, "rx": t, "lag": np.zeros(n_s, np.int32), "bid": mid - 0.01, "ask": mid + 0.01}
    B = {"e": np.array([T0 - 1000]), "rx": np.array([T0 - 1000]), "lag": np.zeros(1, np.int32), "bid": np.array([99.99]), "ask": np.array([100.01])}
    return A, B


def test_level_wave_outliers_lifetimes():
    n_s = 6 * 3600
    outl = [(1 * H + 100_000, 30_000, 0.6), (2 * H + 500_000, 30_000, 0.6), (3 * H + 900_000, 30_000, -0.6), (4 * H, 5_000, 0.6)]
    A, B = books(n_s, 0.5, 0.2, 2.0, outl)
    t1 = T0 + n_s * 1000
    f = bs.pair_features(A, B, T0, t1)
    assert f is not None
    assert abs(f["level_all"] - 0.5) < 0.02                         # уровень = медиана середины
    r, x, k = bs.REFS.index("w20"), bs.XS.index(0.3), bs.KINDS.index("mid")
    h = f["lt"][r, x, k].sum(axis=0)                                # по суткам
    b5, b300 = np.searchsorted(bs.LT_EDGES, 5_000, "right"), np.searchsorted(bs.LT_EDGES, 300_000, "right")
    nz = np.flatnonzero(h)
    assert h.sum() == 4 and nz.min() >= b5 and nz.max() < b300     # 4 выброса (волна 0,2 % порог 0,3 не пересекает); жизнь = до возврата к опоре — чуть больше самого выброса
    assert f["cens"][r, x, k].sum() == 0
    # опора «сутки»: уровень ≈ 0,5, волна ±0,2 даёт отклонения < 0,3 — только выбросы 0,6 (≥ 0,3)
    rd = bs.REFS.index("day")
    assert f["lt"][rd, x, k].sum() == 4
    e = bs.KINDS.index("exec")                                     # Sell ≈ середина − 0,02 %: выброс 0,6 даёт ≈ 0,58 ≥ 0,5 — все четыре
    assert f["lt"][r, bs.XS.index(0.5), e].sum() == 4
    assert f["mins"]["med"].shape == (360,) and abs(np.nanmedian(f["mins"]["med"]) - 0.5) < 0.02
    assert f["sf"].shape == (1, len(bs.LAGS_S), 3)


def test_dead_interval_drops_episode_and_masks_minutes():
    n_s = 3 * 3600
    A, B = books(n_s, 0.0, 0.0, 1.0, [(3600_000, 30_000, 0.6)])
    t1 = T0 + n_s * 1000
    dead = (np.array([T0 + 3540_000], np.int64), np.array([T0 + 3660_000], np.int64))
    f = bs.pair_features(A, B, T0, t1, dead)
    assert f["lt"].sum() == 0 and f["cens"].sum() == 0
    assert np.isnan(f["mins"]["med"][59]) and not np.isnan(f["mins"]["med"][58])
    f2 = bs.pair_features(A, B, T0, t1, None)
    assert f2["lt"][bs.REFS.index("w20"), bs.XS.index(0.3), 0].sum() == 1


def test_open_episode_counted_as_censored():
    n_s = 2 * 3600
    A, B = books(n_s, 0.0, 0.0, 1.0, [(1.5 * H, 10 * H, 0.6)])      # выброс не заканчивается до конца окна
    f = bs.pair_features(A, B, T0, T0 + n_s * 1000)
    assert f["cens"][bs.REFS.index("day"), bs.XS.index(0.3), 0].sum() == 1


def test_ep_groups_one_episode_for_repeated_entries():
    ei = np.array([3, 4, 5, 20, 21])
    xi = np.array([10, 30])
    s, e = bs.ep_groups(ei, xi)
    assert s.tolist() == [3, 20] and e.tolist() == [10, 30]
    s, e = bs.ep_groups(np.array([3, 40]), np.array([10]))
    assert s.tolist() == [3, 40] and e.tolist() == [10, -1]


def test_day_edges_gmt4():
    t0 = 1_794_550_440_000                                           # 13.11 06:14 UTC = 10:14 GMT+4
    e = bs.day_edges(t0, t0 + 50 * H)
    assert e[0] == t0 and len(e) == 4 and (e[1] + bs.TZ_MS) % bs.DAY_MS == 0 and e[2] - e[1] == bs.DAY_MS


def test_rolling_med_pandas_matches_numpy():
    rng = np.random.default_rng(1)
    x = rng.normal(size=300)
    x[50:70] = np.nan
    x[200:203] = np.nan
    for w in (2, 5, 20, 41, 80):
        a, b = bs.rolling_med_centred_np(x, w), bs.rolling_med_centred(x, w)
        assert np.allclose(a, b, equal_nan=True), w
