"""TK-658 §13.58: синтетика для чистых функций `tk658_run` (лента, правда A / V, класс L1, цены входа, формулы ног, Δ*, W*)."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk658_run as R  # noqa: E402


def tape(rows):
    t = np.array([r[0] for r in rows], dtype=np.int64)
    p = np.array([r[1] for r in rows], dtype=float)
    q = np.array([r[2] for r in rows], dtype=float)
    bm = np.array([r[3] for r in rows], dtype=bool)
    return t, p, q, bm


# время, цена, кол-во, is_buyer_maker (False = покупатель-тейкер)
ROWS = [(100, 10.0, 10, False), (102, 9.99, 10, True), (104, 10.01, 5, False),       # ≤ T = 104
        (105, 9.98, 20, True),                                                       # продавец после T — не для лонга
        (110, 10.02, 20, False), (110, 10.03, 30, False),                            # событие покупателя: две цены → L1 точно = 200,4 $
        (110, 9.97, 1, True), (300, 10.05, 100, False)]


def test_proxy_last_side_trade_le_T():
    t, p, q, bm = tape(ROWS)
    j, age = R.proxy(t, p, bm, 1, 104)
    assert p[j] == 10.01 and age == 0
    j, age = R.proxy(t, p, bm, -1, 104)
    assert p[j] == 9.99 and age == 2
    j, age = R.proxy(t, p, bm, 1, 99)
    assert j == -1 and age is None


def test_measure_long_sweep_exact_l1_and_vwap():
    t, p, q, bm = tape(ROWS)
    m = R.measure(t, p, q, bm, 1, 104, 500.0)
    assert m["A"] == 10.02 and m["L_A"] == 6
    assert m["l1"] == "lt" and abs(m["l1_usd"] - 200.4) < 1e-9
    # V: 200,4 $ по 10,02 + 299,6 $ по 10,03 (событие 300,9 $)
    units = 200.4 / 10.02 + 299.6 / 10.03
    assert abs(m["V"] - 500.0 / units) < 1e-12 and m["L_V"] == 6


def test_measure_single_price_classes_and_cap():
    t, p, q, bm = tape([(0, 1.0, 1, False), (10, 2.0, 300, False), (20, 2.0, 100, False)])
    m = R.measure(t, p, q, bm, 1, 5, 500.0)
    assert m["l1"] == "ge" and m["V"] == 2.0 and m["L_V"] == 5
    m = R.measure(t, p, q, bm, 1, 5, 1000.0)
    assert m["l1"] == "unk" and m["V"] is None                         # 800 $ до конца ленты
    m = R.measure(t, p, q, bm, 1, 5, 500.0, cap_ms=4)
    assert m["A"] is None and m["l1"] == "none"


def test_measure_strictly_after_T_and_short_side():
    t, p, q, bm = tape(ROWS)
    m = R.measure(t, p, q, bm, -1, 104, 50.0)
    assert m["A"] == 9.98 and m["L_A"] == 1                             # сделка в T не берётся; первая продавца после T
    assert m["l1"] == "ge" and m["V"] == 9.98


def test_err_sign_worse_positive():
    assert R.err_bp(1, 10.01, 10.0) > 0 and R.err_bp(-1, 9.99, 10.0) > 0
    assert abs(R.err_bp(1, 10.001, 10.0) - 1.0) < 1e-9


def test_entry_e8_matches_engine_trunc():
    assert R.entry_e8(12345678, 1, 1) == 12345678 + 1234
    assert R.entry_e8(12345678, -1, 1) == 12345678 - 1234
    assert R.entry_e8(9999, 1, 1) == 9999


def test_gross_legs_and_shift_identity():
    legs = [[0, 1_0100_0000, 250e6, False], [0, 1_0200_0000, 250e6, True]]
    E = 1_0000_0000
    g = R.gross_legs(legs, 1, E)
    assert abs(g - (250 * 0.01 + 250 * 0.02)) < 1e-9
    assert abs(R.gross_shift(legs, 1, E, 1) - g) < 1e-12
    gs = R.gross_legs([[0, 9900_0000, 500e6, False]], -1, E)
    assert abs(gs - 500 * 0.01) < 1e-9                                  # шорт: 1 − X/E
    assert R.gross_shift(legs, 1, E, 3) < g


def test_choose_delta_and_w_star():
    assert R.choose_delta([10, 60, 90, 120, None]) == (250, False)     # 4 / 5 = 80 % при 250
    assert R.choose_delta([None, None, 2000]) == (1000, True)
    ws, tab = R.w_star([1, 2, 3], [2.0, 3.0, 4.0])
    assert ws == 5 and tab[0]["mean_abs"] == 3.0
    ws, _ = R.w_star([1, 2], [0.5, 0.8])
    assert ws == 5000


def test_lots_of_immediate_lot2_is_one_order():
    rec = dict(entry_ms=10, entry_px_e8=100, legs=[[1, 2, 250.0, False]], lot2=dict(entry_ms=10, entry_px_e8=100, legs=[[1, 2, 250.0, False]]))
    lots = R.lots_of(rec, "S", 1000)
    assert [x[4] for x in lots] == [500.0, 500.0] and lots[1][1] == 1010
    rec["lot2"]["entry_ms"] = 20
    assert [x[4] for x in R.lots_of(rec, "S", 0)] == [250.0, 250.0]
    assert len(R.lots_of(dict(entry_ms=1, entry_px_e8=1, legs=[]), "L", 0)) == 1
