import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import core  # noqa: E402


def mk(t, bid, ask):
    t = np.array(t, dtype=np.int64)
    n = len(t)
    return {"e": t, "rx": t.copy(), "lag": np.zeros(n, np.int32), "bid": np.array(bid, float), "ask": np.array(ask, float), "dep": np.ones(n)}


def test_pair_series_union_and_formulas():
    A = mk([0, 1000, 3000], [100, 101, 99], [100.1, 101.1, 99.1])
    B = mk([500, 2000], [100, 100], [100.1, 100.2])
    T, S, Bu, ia, ib = core.pair_series(A, B, 10_000)
    assert T.tolist() == [500, 1000, 2000, 3000]          # до 500 у B нет цены — точки нет
    assert ia.tolist() == [0, 1, 1, 2] and ib.tolist() == [0, 0, 1, 1]
    # Sell = bid_A/ask_B − 1; Buy = ask_A/bid_B − 1 (bps)
    assert abs(S[1] - (101 / 100.1 - 1) * 1e4) < 1e-9
    assert abs(Bu[3] - (99.1 / 100 - 1) * 1e4) < 1e-9


def test_clean_sorts_and_drops_repeats_and_crossed():
    a = mk([30, 10, 20, 40], [1, 1, 1, 2], [1.1, 1.1, 1.1, 1.0])   # последняя — bid > ask
    c = core.clean(a)
    assert c["e"].tolist() == [10]


def test_episodes_enter_exit_and_censored():
    T = np.arange(10) * 1000
    entry = np.array([0, 1, 1, 0, 0, 1, 0, 0, 1, 0], bool)
    exit_ = np.array([0, 0, 0, 0, 1, 0, 0, 0, 0, 0], bool)
    eps = core.episodes(T, entry, exit_)
    assert eps == [(1, 4), (5, -1)]     # второй вход (5) не закрыт до конца; вход 8 внутри него


def test_rolling_centre_needs_30_bars():
    mins = np.arange(100)
    sb = np.stack([np.zeros(100)] * 4)
    bb = np.stack([np.full(100, 2.0)] * 4)
    m0, c = core.rolling_centre(mins, sb, bb)
    assert m0 == 0 and np.isnan(c[29]) and c[30] == 1.0 and c[99] == 1.0


def test_analyze_pair_finds_known_episode():
    # 100 минут: B постоянна, A стоит на паритете; в минуты 70.0–70.5 A дорожает на 30 bps (5 с, затем возврат)
    t = list(range(0, 100 * 60_000, 1000))
    bidA, askA = [], []
    for x in t:
        bump = 1.003 if 70 * 60_000 <= x < 70 * 60_000 + 5000 else 1.0
        bidA.append(100.0 * bump)
        askA.append(100.02 * bump)
    A, B = mk(t, bidA, askA), mk([0], [100.0], [100.02])
    row, bars, eps, hist = core.analyze_pair(A, B, 40 * 60_000, 100 * 60_000)
    li = core.LEVELS.index(10.0)
    s = row["eps"][li]
    assert s["n"] == 1 and s["conv"] == 1 and s["cens"] == 0
    assert abs(s["tau_s"][0] - 5.0) < 1e-9
    # круг: вход Sell во время всплеска ≈ 27,99 bps, выход Buy на паритете = +2,0 bps → ≈ 26 bps до комиссий
    assert abs(s["gross_med"] - (100.3 / 100.02 - 1) * 1e4 + 2.0) < 0.01 and s["gross_ge_fee"] == 1
    assert row["cross_n"] == 1 and abs(row["cross_dur_ms"][0] - 5000) < 1e-9


def test_ep_table_stats_median_and_outliers():
    # эпизоды: (вход, сторона, схождение мс, не вышел, пик, круг, глубина); круг у не вышедшего — None
    rows = [(0, 1, 1000, False, 14.0, 20.0, 1.0), (0, 1, 3000, False, 16.0, 10.0, 1.0), (0, -1, 9000, False, 30.0, 40.0, 1.0),
            (0, 1, 5000, True, 50.0, None, 1.0)]
    s = core.ep_table_stats(rows)
    assert s["n"] == 4
    assert s["pk"][0] == 23.0 and s["pk"][2] == 50.0 and abs(s["pk"][1] - np.percentile([14, 16, 30, 50], 95)) < 1e-9
    assert s["tau"][0] == 3.0 and abs(s["tau"][1] - np.percentile([1, 3, 9], 95)) < 1e-9   # время — только по вышедшим
    assert s["net"][0] == 20.0 - core.FEE_RT_BPS and abs(s["net"][1] - (np.percentile([20, 10, 40], 95) - core.FEE_RT_BPS)) < 1e-9
    assert s["net250"] is None                                                           # у семи-польных (прежних) эпизодов круга через 250 мс нет
    r8 = [r + (g,) for r, g in zip(rows, (5.0, None, -8.0, None))]                       # 8-е поле — круг через 250 мс; у не вышедшего и у эпизода без цен — None
    n2 = core.ep_table_stats(r8)["net250"]
    assert abs(n2[0] - (np.median([5.0, -8.0]) - core.FEE_RT_BPS)) < 1e-9 and abs(n2[1] - (np.percentile([5.0, -8.0], 95) - core.FEE_RT_BPS)) < 1e-9
    assert core.ep_table_stats([]) == {"n": 0, "pk": None, "tau": None, "net": None, "net250": None}
    assert core.ep_table_stats([(0, 1, 5000, True, 15.0, None, 1.0)])["net"] is None
