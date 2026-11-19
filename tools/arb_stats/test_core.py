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
    tb = list(range(0, 100 * 60_000, 30_000))      # B не меняется, но пишет книгу каждые 30 с (дольше 60 с без записей — котировки нет)
    A, B = mk(t, bidA, askA), mk(tb, [100.0] * len(tb), [100.02] * len(tb))
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
    assert core.ep_table_stats([]) == {"n": 0, "pk": None, "tau": None, "net": None}
    assert core.ep_table_stats([(0, 1, 5000, True, 15.0, None, 1.0)])["net"] is None


def test_stale_leg_gives_no_rows_and_clean_keeps_record_after_pause():
    # A пишет книгу раз в 10 с с той же ценой до 90 с, потом пауза 160 с (дыра сбора), потом снова: повторы снимаются, но время последней сырой записи серии (hold) помнится,
    # а запись после паузы > 60 с остаётся при любой цене
    tA = list(range(0, 100_000, 10_000)) + list(range(250_000, 300_000, 10_000))
    A = core.clean(mk(tA, [100.0] * len(tA), [100.02] * len(tA)))
    assert A["e"].tolist() == [0, 250_000] and A["hold"].tolist() == [90_000, 290_000]
    tB = list(range(0, 300_000, 5_000))
    B = mk(tB, [100.0 + 0.001 * (i % 2) for i in range(len(tB))], [100.02 + 0.001 * (i % 2) for i in range(len(tB))])
    T = core.pair_series(A, B, 400_000)[0]
    assert T[T <= 150_000].tolist() == list(range(0, 150_001, 5_000)) and not ((T > 150_000) & (T < 250_000)).any() and 250_000 in T.tolist()   # котировка A живёт до 90 + 60 = 150 с
    T_old = core.pair_series(A, B, 400_000, stale_ms=None)[0]
    assert ((T_old > 150_000) & (T_old < 250_000)).any()                                                                                     # без ограничения прежнее поведение


def test_gap_does_not_look_like_divergence():
    # A молчит с 40 с до 300 с (последняя цена 100,0); B в 110 с ушла на +40 bps (реальный рост рынка): без ограничения пара видит Sell ≈ −40 bps 190 с — «расхождение» из дыры
    tA = list(range(0, 50_000, 10_000)) + [300_000]
    A = core.clean(mk(tA, [100.0] * 5 + [100.4], [100.02] * 5 + [100.42]))
    tB = list(range(0, 400_000, 5_000))
    pB = [100.0 if t < 110_000 else 100.4 for t in tB]
    B = mk(tB, pB, [p + 0.02 for p in pB])
    T, S, Bu, _, _ = core.pair_series(A, B, 400_000)
    assert abs(S).max() < 5 and abs(Bu).max() < 5 and not ((T > 100_000) & (T < 300_000)).any()
    _, S0, Bu0, _, _ = core.pair_series(A, B, 400_000, stale_ms=None)
    assert S0.min() < -35 and Bu0.min() < -35


def brute_circles(Tv, Sv, Bv, cv, th):
    """Круги по определению, строка за строкой (эталон для Circles): ([(вход, итог)] «до нуля» по направлениям +1 затем −1, [(вход, итог)] «из минуса в плюс»)."""
    zero = []
    for d in (1, -1):
        pos = None
        for k in range(len(Tv)):
            es, eb, dm = Sv[k] - cv[k], cv[k] - Bv[k], (Sv[k] + Bv[k]) / 2 - cv[k]
            if pos is None:
                if (es if d == 1 else eb) >= th:
                    pos = k
            elif (dm <= 0 if d == 1 else dm >= 0):
                zero.append((int(Tv[pos]), float(Sv[pos] - Bv[k]) if d == 1 else float(Sv[k] - Bv[pos])))
                pos = None
    cross, mode, side, pos = [], 0, 0, None
    for k in range(len(Tv)):
        es, eb, dm = Sv[k] - cv[k], cv[k] - Bv[k], (Sv[k] + Bv[k]) / 2 - cv[k]
        if mode == 1 and dm >= 0 or mode == 2 and dm <= 0:
            mode = 0
        if mode == 0:
            if es >= th or eb >= th:
                side, pos, mode = (1 if es >= th else -1), k, 3
        elif mode == 3 and (eb >= th if side == 1 else es >= th):
            cross.append((int(Tv[pos]), float(Sv[pos] - Bv[k]) if side == 1 else float(Sv[k] - Bv[pos])))
            mode = 1 if side == 1 else 2
    return zero, cross


def hand_rows():
    """Рукотворные строки раз в секунду, центр 1,0 bps: нейтраль S = −1, Bu = 3 (середина = центр); «вверх» S = 28, Bu = 32; «вниз» S = −27, Bu = −23."""
    ph = [(0, "n"), (10, "u"), (13, "n"), (16, "d"), (19, "n"), (26, "d"), (29, "n"), (32, "u"), (35, "n"), (41, "n")]
    val = {"n": (-1.0, 3.0), "u": (28.0, 32.0), "d": (-27.0, -23.0)}
    T = np.arange(41) * 1000
    S, B = np.zeros(41), np.zeros(41)
    for (a, k), (b, _) in zip(ph, ph[1:]):
        S[a:b], B[a:b] = val[k]
    return T, S, B, np.full(41, 1.0)


def test_circles_hand_made_zero_and_cross_with_rearm():
    T, S, B, c = hand_rows()
    for cuts in ((41,), (12, 41), (14, 17, 27, 33, 41), tuple(range(1, 41))):                  # один кусок и куски с любыми границами дают то же
        C, a = core.Circles(), 0
        for b in cuts:
            C.step(T[a:b], S[a:b], B[a:b], c[a:b])
            a = b
        for th in (10.0, 15.0, 20.0):
            z, x = C.zacc[th], C.xacc[th]
            # «до нуля»: +1 вход 10 с (Sell 28) → выход 13 с (Buy 3): 25; +1 вход 32 с → 35 с: 25; −1 вход 16 с (Buy −23) → выход 19 с (Sell −1): 22; −1 вход 26 с → 29 с: 22
            assert sorted(zip(z.t.tolist(), z.g.tolist())) == [(10_000, 25.0), (16_000, 22.0), (26_000, 22.0), (32_000, 25.0)], (cuts, th)
            # «из минуса в плюс»: +1 вход 10 с (Sell 28) → выход 16 с (Buy −23): 51; новый вход — только после возврата середины к центру (19 с), поэтому −1 с 26 с (Buy −23) → выход 32 с (Sell 28): 51,
            # а не «переворот» в 16 с
            assert list(zip(x.t.tolist(), x.g.tolist())) == [(10_000, 51.0), (26_000, 51.0)], (cuts, th)
        assert len(C.zacc[30.0].t) == 0 and len(C.xacc[30.0].t) == 0                              # пик 27 bps < 30
        z, x = C.stats()
        assert z[2] == [float(np.median([25, 22, 22, 25]) - core.FEE_RT_BPS), float(np.percentile([25, 22, 22, 25], 95) - core.FEE_RT_BPS), 4] and x[2] == [51.0 - core.FEE_RT_BPS, 51.0 - core.FEE_RT_BPS, 2]
        assert z[3] is None and x[4] is None


def test_circles_open_position_carries_over_pass_boundary():
    T, S, B, c = hand_rows()
    C = core.Circles()
    C.step(T[:12], S[:12], B[:12], c[:12])                      # вход +1 в 10 с, граница прохода внутри вспышки
    assert C.xst[20.0][0] == 3 and C.zop[20.0][0] is not None and len(C.xacc[20.0].t) == 0
    C.step(T[12:], S[12:], B[12:], c[12:])
    assert len(C.xacc[20.0].t) == 2


def test_circles_equal_brute_force_on_random_rows():
    rng = np.random.default_rng(7)
    n = 4000
    T = np.cumsum(rng.integers(1, 800, n)).astype(np.int64)
    mid = np.zeros(n)
    jump = np.where(rng.random(n) < 0.01, rng.choice([-1.0, 1.0], n) * rng.uniform(10, 60, n), 0.0)
    for k in range(1, n):
        mid[k] = 0.9 * mid[k - 1] + jump[k] + rng.normal(0, 1.5)
    half = rng.uniform(0.3, 2.5, n)
    S, B, c = mid - half, mid + half, np.cumsum(rng.normal(0, 0.05, n))
    for th in core.THETAS:
        zero, cross = brute_circles(T, S, B, c, th)
        assert len(cross) > 0 or th >= 50.0
        for cuts in ([n], sorted(set(rng.integers(1, n, 25).tolist())) + [n]):
            C, a = core.Circles(), 0
            for b in cuts:
                C.step(T[a:b], S[a:b], B[a:b], c[a:b])
                a = b
            x, z = C.xacc[th], C.zacc[th]
            assert list(zip(x.t.tolist(), x.g.tolist())) == cross, (th, cuts[:3])
            assert sorted(zip(z.t.tolist(), z.g.tolist())) == sorted(zero), (th, cuts[:3])
