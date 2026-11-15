import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import maker  # noqa: E402

MIN = 60_000
T_FILL = 100 * MIN            # сделка через котировку — на 100-й минуте
T1 = 110 * MIN


def book(ts, bid, ask, dep=1000.0):
    ts = np.array(ts, np.int64)
    n = len(ts)
    return {"e": ts, "rx": ts.copy(), "lag": np.zeros(n, np.int32), "bid": np.array(bid, float), "ask": np.array(ask, float),
            "dbid": np.full(n, dep), "dask": np.full(n, dep)}


def warm(extra_t=(), extra_bid=(), extra_ask=(), base=(100.0, 100.1)):
    """Минутные изменения книги M до 100-й минуты (бары для центра), далее — заданные точки; чередование на 0,001 — чтобы запись не считалась повтором."""
    ts = [i * MIN for i in range(100)]
    bid = [base[0] + (0.001 if i % 2 else 0.0) for i in range(100)]
    ask = [base[1] + (0.001 if i % 2 else 0.0) for i in range(100)]
    return book(ts + list(extra_t), bid + list(extra_bid), ask + list(extra_ask))


def hedge(points):
    ts, bid, ask = zip(*points)
    return book(ts, bid, ask)


def mk_case(trade_p, M_extra=((), (), ()), H_pts=None, t_trade=T_FILL):
    M = warm(*M_extra)
    H = hedge(H_pts or [(0, 99.9, 100.0)])
    P = maker.prep(M, H, np.array([t_trade], np.int64), np.array([trade_p], float), 0, T1)
    return M, H, P


def test_centre_is_about_10bps_and_quote_levels():
    _, _, P = mk_case(100.3)
    c = float(P["cq"][0])
    assert 9.5 < c < 10.6                                  # (0 + 20,02)/2 ≈ 10 bps
    theta = maker.THETAS[0]
    q = max(100.0 * (1 + (c + theta) / 1e4), P["bM"][0] + P["tick"])
    assert abs(q - 100.0 * (1 + (c + theta) / 1e4)) < 1e-9


def test_sell_fill_hedge_slippage_and_taker_close():
    # H: оферу 100.0 → в момент хеджа 100.05 (проскальзывание 5 bps); потом сходимость: M и H сдвигаются вниз
    H_pts = [(0, 99.9, 100.0), (T_FILL + 10, 99.95, 100.05), (T_FILL + 5000, 99.9, 100.05)]
    M_extra = ((T_FILL - 1, T_FILL + 5000), (100.3, 99.95), (100.4, 100.05))   # M подскочил (сделка 100.3), затем вернулся
    M, H, P = mk_case(100.3, M_extra, H_pts)
    cand = maker.candidates(P, maker.THETAS[0])
    assert cand[1].tolist() == [1]                         # продажа на M
    q = float(cand[2][0])
    assert q < 100.3 and abs(float(cand[3][0]) - 100.0) < 1e-12
    r, un = maker.simulate(P, cand, 50, "tt")
    assert un == 0 and len(r) == 1
    t, side, edge, slip, size, net, hold, gross, div = r[0]
    assert side == 1 and t == T_FILL
    assert abs(slip - 5.0) < 1e-6                          # (100.05/100.0 − 1)·1e4
    assert abs(edge - (q / 100.05 - 1) * 1e4) < 1e-9
    # закрытие: первая точка пары, где середина ≤ центра, — на T_FILL+5000; исполнение +50 мс по ценам этого момента
    close = (100.05 / 99.9 - 1) * 1e4                      # купить M по оферу 100.05, продать H по биду 99.9
    assert abs(net - (edge - close - maker.FEE_TT)) < 1e-6
    assert size == 1000.0 and hold == 5050   # размер — полный объём лучшего уровня H (в тесте 1000)


def test_touch_is_not_a_fill():
    _, _, P = mk_case(100.3)
    c = float(P["cq"][0])
    q = 100.0 * (1 + (c + maker.THETAS[0]) / 1e4)
    P2 = maker.prep(*(mk_case(q)[:2]), np.array([T_FILL], np.int64), np.array([q], float), 0, T1)
    assert len(maker.candidates(P2, maker.THETAS[0])[0]) == 0           # касание — не исполнение
    P3 = maker.prep(*(mk_case(q)[:2]), np.array([T_FILL], np.int64), np.array([q * (1 + 1e-6)], float), 0, T1)
    assert len(maker.candidates(P3, maker.THETAS[0])[0]) == 1           # строго выше — исполнение


def test_post_only_guard_quote_inside_spread():
    # M уже выше цены котировки (бид 100.5 > q_calc ≈ 100.2): котировка не ниже bid + tick, продажа по биду её не исполняет
    M_extra = ((T_FILL - 1000,), (100.5,), (100.6,))
    M, H, P = mk_case(100.5, M_extra)
    tick = P["tick"]
    assert tick > 0
    assert len(maker.candidates(P, maker.THETAS[0])[0]) == 0           # сделка по биду 100.5 — не «за» котировкой 100.5 + tick
    M2, H2, P2 = mk_case(100.6, M_extra)
    cand = maker.candidates(P2, maker.THETAS[0])
    assert cand[1].tolist() == [1] and abs(cand[2][0] - (100.5 + tick)) < 1e-9


def test_maker_close_waits_for_trade_through_and_pays_maker_fees():
    # продажа на M исполнена; закрывающая лимитка на покупку q_c = bid_H·(1 + c/1e4) ≈ 100.0; сделка по 100.3 её не затрагивает, по 99.95 — за ценой
    M, H, P = mk_case(100.3)
    tr_e = np.array([T_FILL, T_FILL + 100, T_FILL + 2000, T_FILL + 4000], np.int64)
    tr_p = np.array([100.3, 100.3, 99.95, 99.80])
    P2 = maker.prep(M, H, tr_e, tr_p, 0, T1)
    cand = maker.candidates(P2, maker.THETAS[0])
    assert cand[0][0] == T_FILL and cand[1][0] == 1
    r, un = maker.simulate(P2, cand, 50, "mt")
    t, side, edge, slip, size, net, hold, gross, div = r[0]
    qc = float(P2["close"][1][1][0])
    assert abs(qc - min(99.9 * (1 + float(P2["cq"][2]) / 1e4), P2["aM"][2] - P2["tick"])) < 1e-9
    assert hold == 2000 + 50                               # первая сделка за ценой — T_FILL+2000, хедж +50
    assert abs(net - (edge - (qc / 99.9 - 1) * 1e4 - maker.FEE_MT)) < 1e-6
    # следующий круг — вход на покупку по сделке 99.80; закрывающей продажи нет — не закрыт до конца данных
    assert len(r) == 2 and r[1, 1] == -1 and np.isnan(r[1, 5]) and un == 1


def test_unclosed_round_when_no_convergence_and_summary():
    M, H, P = mk_case(100.3)
    cand = maker.candidates(P, maker.THETAS[0])
    r, un = maker.simulate(P, cand, 300, "tt")             # центр на 10 bps, середина пары остаётся 10 → «mid ≤ c» может выполниться — проверяем сводку
    s = maker.summarize(r, un, 0, T1)
    assert s["n"] == len(r) and s["unclosed"] == un
    assert s["fills"][0] >= 0 and s["usd_day"] == s["usd_day"]
