import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import cross  # noqa: E402
import maker  # noqa: E402
import outlier  # noqa: E402
from test_maker import T1, T_FILL, hedge, warm  # noqa: E402

# A — книга «M» из test_maker (центр ≈ 10 bps при B = 99.9/100.0), B стоит. Sell = bid_A/100.0 − 1, Buy = ask_A/99.9 − 1.
UP = (T_FILL, 100.4, 100.5)                  # Sell = +40 bps → вход side +1 (продали A), к центру ≈ +30
DOWN = (T_FILL + 5000, 99.55, 99.65)         # Buy = −25 bps → c − Buy ≈ 35: выход «через центр» при θ_exit ≤ 35
UP2 = (T_FILL + 10_000, 100.4, 100.5)        # снова Sell = +40: S − c ≈ 30


def pair(pts, H_pts=((0, 99.9, 100.0),)):
    ts, bid, ask = zip(*pts)
    return outlier.prep_tt(warm(ts, bid, ask), hedge(list(H_pts)), 0, T1)


def test_cross_exit_gross_is_entry_plus_exit_side_and_threshold():
    P = pair([UP, DOWN])
    ent = outlier.entries_for(P, 20.0)
    r, un = cross.simulate_cross(P, ent, 0, 30.0)
    assert un == 0 and r.shape[1] == cross.NC
    t, side, edge, slip, size, net, hold, gross, div, tx, te = r[0]
    assert side == 1 and t == T_FILL and abs(edge - 40.0) < 1e-6 and hold == 5000 and tx == T_FILL and te == T_FILL + 5000
    assert abs(gross - (40.0 - (99.65 / 99.9 - 1) * 1e4)) < 1e-6 and abs(net - (gross - 12.6)) < 1e-9       # 40 − (−25) = 65
    assert len(r) == 1                                                                                         # возврата через центр нет — новых входов нет
    r2, un2 = cross.simulate_cross(P, ent, 0, 40.0)                                                           # c − Buy ≈ 35 < 40: до конца окна не вышли
    assert un2 == 1 and len(r2) == 1 and np.isnan(r2[0, 5]) and np.isnan(r2[0, 10])


def test_without_rearm_same_signal_reopens_and_flip_chains_prices():
    P = pair([UP, DOWN, UP2])
    ent = outlier.entries_for(P, 20.0)
    r, _ = cross.simulate_cross(P, ent, 0, 20.0, rearm=False)
    assert len(r) >= 2 and r[1, 1] == -1 and r[1, 0] == T_FILL + 5000        # без «переармирования» тот же сигнал открывает обратную позицию сразу
    f, un = cross.simulate_flip(P, ent, 0, 20.0)
    assert un == 1 and len(f) == 3 and f[:, 1].tolist() == [1, -1, 1] and np.isnan(f[2, 5])
    # цена закрытия = цена входа обратной позиции: gross_k = side_k · (edge_k − edge_{k+1})
    for k in range(2):
        assert abs(f[k, 7] - f[k, 1] * (f[k, 2] - f[k + 1, 2])) < 1e-9
    assert f[1, 0] == T_FILL + 5000 and f[1, 9] == T_FILL + 5000 and f[0, 10] == f[1, 9]
    # «через центр» с переармированием: обратный вход на +5000 (середина ещё внизу) запрещён, следующий — только после возврата через центр (UP2)
    r3, _ = cross.simulate_cross(P, ent, 0, 20.0, rearm=True)
    assert len(r3) == 2 and r3[1, 0] == T_FILL + 10_000 and r3[1, 1] == 1
    r4, _ = cross.simulate_cross(pair([UP, DOWN]), outlier.entries_for(pair([UP, DOWN]), 20.0), 0, 20.0, rearm=True)
    assert len(r4) == 1                                                          # середина вниз и не вернулась: новых входов нет


def test_latency_moves_both_fills():
    P = pair([UP, (T_FILL + 200, 100.0, 100.1), DOWN, (T_FILL + 5200, 99.95, 100.05)])
    ent = outlier.entries_for(P, 20.0)
    r, _ = cross.simulate_cross(P, ent, 300, 30.0)
    t, side, edge, slip, size, net, hold, gross, div, tx, te = r[0]
    assert tx == T_FILL + 300 and abs(edge - (100.0 / 100.0 - 1) * 1e4) < 1e-6            # вход — по ценам через 300 мс (выброс уже ушёл)
    assert te - t == hold


def test_entry_matches_baseline_first_circle():
    P = pair([UP, (T_FILL + 3000, 99.95, 100.05), DOWN])
    ent = outlier.entries_for(P, 20.0)
    for lat in (0, 50, 300):
        b, _ = outlier.simulate_tt(P, ent, lat, "full")
        c, _ = cross.simulate_cross(P, ent, lat, 30.0)
        assert np.allclose(b[0, :5], c[0, :5]) and abs(b[0, 8] - c[0, 8]) < 1e-9


def mk_exit_case(tr_e, tr_p, m_is_A=True):
    P = pair([UP, DOWN])
    return P, cross.prep_exit_mk(P, np.array(tr_e, np.int64), np.array(tr_p, float), m_is_A)


def test_maker_exit_limit_price_fill_strictly_through_and_fees():
    # M = A: продали A на 100.4, закрытие — лимитка на покупку A с запасом c − x; хедж — продажа B по биду 99.9 через L
    P, Q = mk_exit_case([T_FILL + 100, T_FILL + 6000, T_FILL + 9000], [99.90, 99.40, 99.30])
    ent = outlier.entries_for(P, 20.0)
    c = float(Q["cq"][1])
    q30 = min(99.9 * (1 + (c - 30.0) / 1e4), float(Q["aA"][1]) - Q["tick"])
    cc = cross.close_cands(Q, 30.0)[1]
    assert abs(cc[1][0] - q30) < 1e-9 and cc[0][0] == T_FILL + 6000            # 99.90 не проходит за ценой ≈ 99.7, 99.40 — проходит
    r, un = cross.simulate_cross_mk(P, ent, 50, Q, 30.0, True)
    t, side, edge, slip, size, net, hold, gross, div, tx, te = r[0]
    assert un == 0 and te == T_FILL + 6000 + 50
    assert abs(gross - (edge - (q30 / 99.9 - 1) * 1e4)) < 1e-6 and abs(net - (gross - cross.FEE_MX)) < 1e-9 and abs(cross.FEE_MX - 10.71) < 1e-9
    # касание цены — не исполнение
    P2, Q2 = mk_exit_case([T_FILL + 6000], [q30])
    assert len(cross.close_cands(Q2, 30.0)[1][0]) == 0


def test_maker_exit_on_leg_b_mirrors_limit_side():
    P, Q = mk_exit_case([T_FILL + 6000], [99.0], m_is_A=False)
    c = float(Q["cq"][0])
    q = max(float(Q["aA"][0]) / (1 + (c - 30.0) / 1e4), float(Q["bB"][0]) + Q["tick"])
    cc = cross.close_cands(Q, 30.0)[1]
    assert len(cc[0]) == 0                                               # продажа B лимиткой выше рынка: сделка по 99.0 её не исполняет
    P, Q = mk_exit_case([T_FILL + 6000], [q * 1.01], m_is_A=False)
    cc = cross.close_cands(Q, 30.0)[1]
    assert len(cc[0]) == 1 and abs(cc[1][0] - q) < 1e-9


def test_mk_x0_equals_maker_simulate():
    from test_maker import mk_case
    M, H, _ = mk_case(100.3)
    tr_e = np.array([T_FILL, T_FILL + 100, T_FILL + 2000, T_FILL + 4000], np.int64)
    tr_p = np.array([100.3, 100.3, 99.95, 99.80])
    P = cross.prep_mk_x(M, H, tr_e, tr_p, 0, T1)
    cand = maker.candidates(P, maker.THETAS[0])
    for var in ("tt", "mt"):
        for lat in (50, 300):
            a, ua = maker.simulate(P, cand, lat, var)
            b, ub = cross.simulate_mk_x(P, cand, lat, var, 0.0, False)
            assert ua == ub and np.allclose(a, b[:, :9], equal_nan=True)
            assert np.allclose(b[:, 9], b[:, 0]) and np.allclose(b[~np.isnan(b[:, 10]), 10], (b[:, 0] + b[:, 6])[~np.isnan(b[:, 10])])


def test_mk_cross_exit_waits_for_opposite_signal():
    from test_maker import mk_case
    H_pts = [(0, 99.9, 100.0)]
    # скачок на 100.3 (вход), на +2000 середина вернулась к центру (Buy = +15), на +5000 — Buy = −25 bps
    M_extra = ((T_FILL - 1, T_FILL + 2000, T_FILL + 5000), (100.3, 99.95, 99.55), (100.4, 100.05, 99.65))
    M, H, _ = mk_case(100.3, M_extra, H_pts)
    P = cross.prep_mk_x(M, H, np.array([T_FILL], np.int64), np.array([100.3]), 0, T1)
    cand = maker.candidates(P, maker.THETAS[0])
    r0, _ = cross.simulate_mk_x(P, cand, 50, "tt", 0.0, False)
    rx, _ = cross.simulate_mk_x(P, cand, 50, "tt", 20.0, True)
    assert r0[0, 6] == 2050 and rx[0, 6] == 5050                                   # к центру — на +2000, через центр — на +5000
    assert abs(rx[0, 7] - (rx[0, 2] - (99.65 / 99.9 - 1) * 1e4)) < 1e-6 and rx[0, 7] > r0[0, 7]


def test_funding_bps_signs_and_boundaries():
    arr = np.zeros((3, 11))
    arr[:, 1] = [1, -1, 1]
    arr[:, 9] = [100, 100, 100]
    arr[:, 10] = [250, 250, np.nan]
    f1 = cross.funding_cum([[100, 0.001], [200, 0.002], [300, 0.004]])         # отметка в момент входа не считается, в момент выхода — считается
    f2 = cross.funding_cum([[200, 0.0005]])
    out = cross.funding_bps(arr, f1, f2)
    # (100, 250]: первая биржа — 0.002; вторая — 0.0005. side +1: шорт на первой получает 0.002, лонг на второй платит 0.0005 → +0.0015
    assert abs(out[0] - 15.0) < 1e-9 and abs(out[1] + 15.0) < 1e-9 and np.isnan(out[2])
    assert np.isnan(cross.funding_bps(arr, None, f2)).all()
