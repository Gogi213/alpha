"""Тесты tk616_maker.py (TK-616 §13.28 п. 5–6) на синтетике — без движка и без P&L: клетки и vid, месяцы, порог Холма, G_mk, разбор «выбор по сигналам».
Без pytest: `PYTHONUTF8=1 python3 tools/p42/tests/test_tk616_maker.py`.
"""
import os
import sys
import traceback

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import retitr_full as rf  # noqa: E402
import retitr_full_run as rr  # noqa: E402
import tk616_maker as mk  # noqa: E402

NB = 2000


def test_cells_are_the_bases_plus_mk_key_with_distinct_vids():
    spec = rf.Spec.load(rr.GRIDS)
    cs, bs = mk.cells(spec)
    assert sorted(cs) == ["L_mk1", "L_mk2", "S_mk1", "S_mk2"] and sorted(bs) == ["L", "S"]
    vids = [r.vid for r in list(cs.values()) + list(bs.values())]
    assert len(set(vids)) == 6
    for i, r in cs.items():
        b = bs[r.side]
        assert r.side == i[0] and r.engine_keys == b.engine_keys + " mk=%s" % i[-1] and not r.sig
    assert bs["L"].engine_keys.endswith("rf=0.2 rs=20") and "bm=1" in bs["S"].engine_keys    # базы §13.24, дословно


def test_month_rows_sum_to_the_total_and_per_day_uses_month_length():
    S = np.arange(rf.NDAYS, dtype=float)
    N = np.ones(rf.NDAYS)
    rows = mk.month_rows(S, N)
    assert [r["month"] for r in rows] == list(rf.MONTH_NAMES) + ["all"]
    assert np.isclose(sum(r["sum"] for r in rows[:-1]), rows[-1]["sum"]) and sum(r["n"] for r in rows[:-1]) == rows[-1]["n"] == rf.NDAYS
    assert np.isclose(rows[0]["per_day"], S[:31].sum() / 31) and np.isclose(rows[4]["mean"], S[-13:].mean())


def test_holm_thresholds_are_stepdown_0125_0167_025_05_by_rank():
    ps = [0.30, 0.001, 0.02, 0.011]
    rej, thr = mk.holm_threshold(ps)
    assert np.allclose(sorted(thr), [0.05 / 4, 0.05 / 3, 0.05 / 2, 0.05])
    assert thr[1] == 0.05 / 4 and thr[3] == 0.05 / 3 and thr[2] == 0.05 / 2 and thr[0] == 0.05
    assert rej == [False, True, True, True]                                  # 0,001 ≤ 0,0125; 0,011 ≤ 0,0167; 0,02 ≤ 0,025; 0,30 > 0,05
    rej2, _ = mk.holm_threshold([0.30, 0.001, 0.02, 0.2])
    assert rej2 == [False, True, False, False]                                # 0,02 > 0,0167: шаг останавливается на первом непрошедшем


def _daily(rng, mean, sd=3.0, per_day=3):
    n = np.full(rf.NDAYS, float(per_day))
    s = rng.normal(mean * per_day, sd, size=rf.NDAYS)
    return s, n


def test_gmk_passes_a_strong_cell_and_rejects_zero_and_worse_than_base():
    rng = np.random.default_rng(7)
    base = {"L": _daily(rng, 0.0), "S": _daily(rng, 0.2)}
    cells = {"L_mk1": dict(side="L", S=_daily(rng, 1.5)[0], N=np.full(rf.NDAYS, 3.0)),       # сильно положительна и лучше базы
             "L_mk2": dict(side="L", S=_daily(rng, 0.0)[0], N=np.full(rf.NDAYS, 3.0)),       # ≈ 0
             "S_mk1": dict(side="S", S=_daily(rng, 1.5)[0], N=np.full(rf.NDAYS, 3.0)),
             "S_mk2": dict(side="S", S=_daily(rng, -0.5)[0], N=np.full(rf.NDAYS, 3.0))}      # отрицательна
    res = mk.gmk(cells, base, nboot=NB)
    assert res["L_mk1"]["pass"] and res["S_mk1"]["pass"] and res["L_mk1"]["g1"] and res["L_mk1"]["g2"] and res["L_mk1"]["g3"]
    assert not res["L_mk2"]["pass"] and not res["S_mk2"]["pass"] and not res["S_mk2"]["g1"]
    assert res["L_mk1"]["positive_months"] == 5 and res["L_mk1"]["counted_months"] == 5
    assert sorted(g["holm_threshold"] for g in res.values()) == sorted([0.0125, 0.05 / 3, 0.025, 0.05])
    for g in res.values():
        assert np.isclose(g["delta_sum"], g["sum"] - g["sum_base"]) and np.isclose(g["delta_mean"], g["mean"] - g["mean_base"])


def test_gmk_g2_counts_only_months_with_five_trades_and_g3_needs_better_mean_than_the_base():
    rng = np.random.default_rng(3)
    S, N = _daily(rng, 1.5)
    base = {"L": (S.copy(), N.copy()), "S": (S.copy(), N.copy())}                             # база равна клетке: среднее не больше базы → g3 нет
    cells = {"L_mk1": dict(side="L", S=S.copy(), N=N.copy())}
    g = mk.gmk(cells, base, nboot=NB)["L_mk1"]
    assert not g["g3"] and not g["pass"]
    N2 = N.copy()
    N2[-13:] = 0.0                                                                            # ноябрь без сделок: месяц не учитывается
    S2 = S.copy()
    S2[-13:] = 0.0
    g = mk.gmk({"L_mk1": dict(side="L", S=S2, N=N2)}, {"L": (np.zeros(rf.NDAYS), np.full(rf.NDAYS, 3.0)), "S": base["S"]}, nboot=NB)["L_mk1"]
    assert g["counted_months"] == 4 and g["positive_months"] == 4 and g["g2"]


def test_classify_splits_delta_into_execution_and_selection_with_identity():
    W = "wa"
    k = lambda i: (W, "S%d" % i, "1", i)                                                       # noqa: E731
    C = {k(1): (0, 2.0), k(2): (0, -1.0), k(5): (1, 3.0)}
    B = {k(1): (0, 1.0), k(3): (0, 4.0), k(4): (0, 5.0), k(6): (0, 7.0), k(7): (0, 2.5)}
    R = {k(3): ("unfilled", 0), k(4): ("touched", 0), k(6): ("notick", 0), k(8): ("unfilled", 1)}
    out = mk.classify(C, B, R)
    a, tot = out[0], out["all"]
    assert a["both"] == 1 and a["exec_effect"] == 1.0 and a["cell_only"] == 1 and a["cell_only_c"] == -1.0
    assert a["base_only"]["unfilled"] == [1, 4.0] and a["base_only"]["touched"] == [1, 5.0] and a["base_only"]["notick"] == [1, 7.0] and a["base_only"]["displaced"] == [1, 2.5]
    assert out[1]["cell_only"] == 1 and out[1]["rej"]["unfilled"] == 1 and tot["rej"] == {"unfilled": 2, "touched": 1, "notick": 1}
    for o in out.values():
        assert np.isclose(o["delta"], o["exec_effect"] + o["select_effect"])
    assert np.isclose(tot["delta"], sum(v[1] for v in C.values()) - sum(v[1] for v in B.values()))


def test_go_guard_and_root_layout():
    try:
        mk.go_required({})
    except rf.GuardError as e:
        assert "TK616_GO" in str(e)
    else:
        raise AssertionError("ожидалась охрана")
    mk.go_required({"TK616_GO": "1"})


def main():
    fails = 0
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for n, f in tests:
        try:
            f()
            print("ok   ", n)
        except Exception as e:  # noqa: BLE001
            fails += 1
            print("FAIL ", n, "-", type(e).__name__, e)
            tb = traceback.extract_tb(e.__traceback__)
            print("       %s:%d  %s" % (os.path.basename(tb[-1].filename), tb[-1].lineno, tb[-1].line))
    print("%d тестов, %d провалов" % (len(tests), fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
