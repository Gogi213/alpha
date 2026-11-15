import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import outlier  # noqa: E402
from test_maker import MIN, T1, T_FILL, book, hedge, warm  # noqa: E402


def mk_pair(M_pts, H_pts=((0, 99.9, 100.0),)):
    """M — «A» (минутные изменения до 100-й минуты для центра ≈ 10 bps, далее M_pts: (t, bid, ask)); H — «B» (стоит)."""
    ts, bid, ask = zip(*M_pts) if M_pts else ((), (), ())
    A, B = warm(ts, bid, ask), hedge(list(H_pts))
    return outlier.prep_tt(A, B, 0, T1)


JUMP = [(T_FILL, 100.4, 100.5)]


def test_sell_entry_full_convergence_l0():
    P = mk_pair(JUMP + [(T_FILL + 5000, 99.95, 100.05)])
    ent = outlier.entries_for(P, 20.0)
    r, un = outlier.simulate_tt(P, ent, 0, "full")
    assert un == 0 and r.shape == (1, 9)
    t, side, edge, slip, size, net, hold, gross, div = r[0]
    assert side == 1 and t == T_FILL and abs(edge - (100.4 / 100.0 - 1) * 1e4) < 1e-6 and slip == 0
    close = (100.05 / 99.9 - 1) * 1e4
    assert abs(gross - (edge - close)) < 1e-6 and abs(net - (gross - outlier.FEE_TT4)) < 1e-9
    assert hold == 5000 and size == 1000.0 and 29.9 < div < 30.1          # расхождение к центру ≈ 40 − 10 bps


def test_threshold_not_reached_no_entry_and_buy_side():
    P = mk_pair([(T_FILL, 100.2, 100.3), (T_FILL + 5000, 99.95, 100.05)])        # Sell = 20 bps, к центру ≈ 10 < θ
    assert outlier.simulate_tt(P, outlier.entries_for(P, 20.0), 0, "full")[0].shape == (0, 9)
    P = mk_pair([(T_FILL, 99.5, 99.6), (T_FILL + 5000, 99.95, 100.05)])          # Buy = (99.6/99.9 − 1) = −30 bps: c − Buy = 40
    r, _ = outlier.simulate_tt(P, outlier.entries_for(P, 20.0), 0, "full")
    assert r[0, 1] == -1 and abs(r[0, 2] - (99.6 / 99.9 - 1) * 1e4) < 1e-6


def test_latency_executes_at_later_prices_and_slip():
    # M вернулся на 200 мс после сигнала; при L = 300 вход идёт по ценам уже после возврата (запаса нет)
    P = mk_pair(JUMP + [(T_FILL + 200, 100.0, 100.1), (T_FILL + 5000, 99.95, 100.05)])
    r, _ = outlier.simulate_tt(P, outlier.entries_for(P, 20.0), 300, "full")
    t, side, edge, slip, *_ = r[0]
    assert abs(edge - (100.0 / 100.0 - 1) * 1e4) < 1e-6 and abs(slip - 40.0) < 1e-6


def test_half_convergence_closes_earlier():
    pts = JUMP + [(T_FILL + 2000, 100.15, 100.25), (T_FILL + 5000, 99.95, 100.05)]
    P = mk_pair(pts)
    ent = outlier.entries_for(P, 20.0)
    full, _ = outlier.simulate_tt(P, ent, 0, "full")
    half, _ = outlier.simulate_tt(P, ent, 0, "half")
    assert full[0, 6] == 5000 and half[0, 6] == 2000
    assert abs(half[0, 7] - (half[0, 2] - (100.25 / 99.9 - 1) * 1e4)) < 1e-6


def test_unclosed_and_one_position_at_a_time():
    P = mk_pair(JUMP)                                                           # M не возвращается
    r, un = outlier.simulate_tt(P, outlier.entries_for(P, 20.0), 0, "full")
    assert un == 1 and len(r) == 1 and np.isnan(r[0, 5])
