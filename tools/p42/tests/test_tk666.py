"""Тесты §13.66 (TK-666): круглое число (ρ, мантисса, знак цены), фандинг на сигнале (последняя выплата ≤ t, дыра > 2 интервалов, привязка к 8 ч), премия (закрытая свеча, возраст ≤ 5 мин, мусор после сигнала),
слияние уровня (версия цены ≤ m, живой уровень, допуск τ_c, мусор после m), 16 клеток на сторону (4 пробные / 8 соседей / 4 обратные), маски (строго меньше порога, пусто проходит, обратные), допуск до P&L (подмножество),
шумовая планка, Холм 61 + m_new.
Синтетика, без движка, без P&L.
Без pytest: `PYTHONUTF8=1 python3 tools/p42/tests/test_tk666.py` — выполняет все test_* и печатает итог."""
import os
import sys
import traceback

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import tk604_stats as st  # noqa: E402
import tk666_feat as ft  # noqa: E402
import tk666_run as r6  # noqa: E402

NAN = float("nan")


def close(a, b, tol=1e-9):
    return abs(a - b) <= tol * max(1.0, abs(b))


# ---- R: круглое число -------------------------------------------------------------------------------------------------------------

def test_round_feat_btc_alt_and_sign():
    natr = int(0.004 * 1e8)
    rho, mant = ft.round_feat(7752800000000, natr)          # 77 528 → шаг 1 000, ближайшее 78 000, расстояние 472
    assert close(rho, 472 / (0.004 * 77528)) and close(mant, 7.7528)
    rho, mant = ft.round_feat(42980000, natr)                # 0,4298 → шаг 0,01, ближайшее 0,43, расстояние 0,0002
    assert close(rho, 0.0002 / (0.004 * 0.4298)) and close(mant, 4.298)
    rho, mant = ft.round_feat(510000000, natr)               # 5,1 → шаг 0,1, на круглом числе
    assert rho == 0.0 and close(mant, 5.1)
    for px in (7752800000000, 42980000, 510000000):          # знак цены не важен (|px|, условие критика 1)
        a, b = ft.round_feat(px, natr), ft.round_feat(-px, natr)
        assert a == b, (px, a, b)
    assert np.isnan(ft.round_feat(0, natr)[0]) and np.isnan(ft.round_feat(510000000, 0)[0])


# ---- F: фандинг -------------------------------------------------------------------------------------------------------------------

def test_funding_at_last_payment_gap_tie():
    h = 3.6e6
    t = np.array([0, 8 * h, 16 * h], dtype=np.int64)
    rate8 = np.array([1e-4, 2e-4, -3e-4])
    inter = np.array([8.0, 8.0, 8.0])
    assert np.isnan(ft.funding_at(t, rate8, inter, -1)[0])                           # до первой выплаты — пусто
    assert ft.funding_at(t, rate8, inter, int(8 * h))[0] == 2e-4                     # выплата ровно в t входит (calc_time ≤ t)
    assert ft.funding_at(t, rate8, inter, int(8 * h) - 1)[0] == 1e-4
    f, age = ft.funding_at(t, rate8, inter, int(16 * h + 15.9 * h))                  # 15,9 ч после выплаты ≤ 2 интервалов (16 ч)
    assert f == -3e-4 and close(age, 15.9)
    assert np.isnan(ft.funding_at(t, rate8, inter, int(16 * h + 16.1 * h))[0])      # дыра > 2 интервалов — пусто
    assert np.isnan(ft.funding_at(np.zeros(0, dtype=np.int64), np.zeros(0), np.zeros(0), 5)[0])


def test_funding_causal_garbage_after_sig():
    rng = np.random.default_rng(666)
    h = 3.6e6
    t = np.arange(0, 40) * int(8 * h)
    rate8 = rng.normal(0, 1e-4, len(t))
    inter = np.full(len(t), 8.0)
    for k in range(5, 35):
        ms = int(t[k] + 1000 * rng.integers(1, 20000))
        clean = ft.funding_at(t, rate8, inter, ms)
        after = t > ms
        r2, i2 = rate8.copy(), inter.copy()
        r2[after], i2[after] = rng.normal(0, 1, int(after.sum())), 1.0
        assert ft.funding_at(t, r2, i2, ms) == clean


def test_interval_rounding():
    assert ft.interval_round(7.99) == 8.0 and ft.interval_round(4.0) == 4.0 and ft.interval_round(1.1) == 1.0 and ft.interval_round(3.2) == 4.0 and ft.interval_round(2.4) == 2.0


# ---- P: премия --------------------------------------------------------------------------------------------------------------------

class FakePrem(ft.Prem):
    def __init__(self, days):
        super().__init__()
        self.days = days

    def day(self, sym, day):
        return self.days.get((sym, day))


def test_premium_closed_candle_and_age():
    base = ft.LAST_MS - 86_400_000 * 5
    day = ft.day_of(base)
    o0 = base // 60_000 * 60_000
    p = FakePrem({("X", day): {o0: 0.001, o0 + 60_000: 0.002, o0 + 120_000: 0.003}})
    v, age = p.at("X", o0 + 120_000)                        # свеча o0+60 000 закрылась ровно в t → берётся
    assert v == 0.002 and age == 0.0
    v, age = p.at("X", o0 + 120_000 + 30_000)               # свеча o0+120 000 ещё не закрыта (закроется через 30 с) → предыдущая
    assert v == 0.002 and close(age, 30.0)
    assert np.isnan(p.at("X", o0 + 120_000 + 400_000)[0])   # последняя свеча старше 5 мин
    v, age = p.at("X", o0 + 60_000 + 60_000 + 299_000)      # свеча o0+60 000 закрылась 299 с назад — ещё в пределах 5 мин
    assert v == 0.002 or v == 0.003 or age <= 300.0
    assert np.isnan(FakePrem({}).at("X", o0)[0])             # нет файла суток


def test_premium_garbage_after_sig_unchanged():
    rng = np.random.default_rng(666)
    base = ft.LAST_MS - 86_400_000 * 5
    day = ft.day_of(base)
    o0 = base // 60_000 * 60_000
    d = {o0 + 60_000 * i: float(rng.normal(0, 1e-3)) for i in range(30)}
    ms = o0 + 60_000 * 20 + 17_000
    clean = FakePrem({("X", day): d}).at("X", ms)
    d2 = {o: (float(rng.normal(0, 1)) if o + 60_000 > ms else v) for o, v in d.items()}
    assert FakePrem({("X", day): d2}).at("X", ms) == clean


def test_guard_blocks_ge_14_11():
    try:
        ft.check_ms(ft.LAST_MS)
    except SystemExit:
        pass
    else:
        raise AssertionError("охрана 14.11 не сработала")
    ft.check_ms(ft.LAST_MS - 1)


# ---- C: слияние -------------------------------------------------------------------------------------------------------------------

def test_level_px_at_versions_and_alive():
    lv = (100, 500, [100, 200, 300], [1000, 1010, 1020])
    assert ft.level_px_at(lv, 99) is None and ft.level_px_at(lv, 500) is None             # не живой: до рождения / с конца
    assert ft.level_px_at(lv, 100) == 1000 and ft.level_px_at(lv, 199) == 1000
    assert ft.level_px_at(lv, 200) == 1010 and ft.level_px_at(lv, 499) == 1020           # версия со временем ≤ m, не позже


def test_confluence_tau_and_empty():
    levels = {"X": [(0, 1000, [0], [100_000]), (0, 1000, [0], [100_150])], "Y": []}
    c = ft.confluence(levels, "X", 10, 100_000)              # P240 = 100 000 (0 %) → все τ; 100 150 (0,15 %) → τ ≥ 0,2 %
    assert c == [1.0, 1.0, 1.0]
    levels = {"X": [(0, 1000, [0], [100_150])]}
    assert ft.confluence(levels, "X", 10, 100_000) == [0.0, 1.0, 1.0]
    levels = {"X": [(0, 1000, [0], [100_300])]}
    assert ft.confluence(levels, "X", 10, 100_000) == [0.0, 0.0, 1.0]
    assert ft.confluence({}, "X", 10, 100_000) == [0.0, 0.0, 0.0]
    assert ft.confluence({"X": [(0, 1000, [0], [100_000])]}, "X", 10, -100_000) == [1.0, 1.0, 1.0]        # знак цены не важен
    assert ft.confluence({"X": [(50, 1000, [50], [100_000])]}, "X", 10, 100_000) == [0.0, 0.0, 0.0]       # уровень ещё не родился на m


def test_confluence_garbage_versions_after_m_unchanged():
    rng = np.random.default_rng(666)
    lv = {"X": [(0, 10_000, [0, 50, 90], [100_000, 100_100, 99_000])]}
    for m in (10, 49, 50, 60, 89):
        clean = ft.confluence(lv, "X", m, 100_050)
        dirty = {"X": [(0, 10_000, [0, 50, 90], [p if t <= m else int(rng.integers(1, 10 ** 9)) for t, p in zip([0, 50, 90], [100_000, 100_100, 99_000])])]}
        assert ft.confluence(dirty, "X", m, 100_050) == clean, m


# ---- клетки, маски, допуск ----------------------------------------------------------------------------------------------------------

def test_cell_specs_16_4_8_4():
    sp = r6.cell_specs()
    assert len(sp) == 16 and [c["kind"] for c in sp].count("T") == 4 and [c["kind"] for c in sp].count("N") == 8 and [c["kind"] for c in sp].count("V") == 4
    assert [c["cid"] for c in sp if c["kind"] == "T"] == ["F25", "P25", "C02", "R05"]
    groups = {c["group"] for c in sp if c["kind"] == "T"}
    assert groups == {1, 2, 3, 4} and all(c["group"] in groups for c in sp if c["kind"] == "N")
    assert len({c["cid"] for c in sp}) == 16


def test_masks_strict_nan_passes_reverse():
    gF = np.array([-3.0, -2.0, -1.0, NAN, 0.0, 1.0])
    d = {"gF": gF, "gP": gF.copy(), "conf02": np.array([0, 1, 0, NAN, 1, 0], dtype=float), "conf01": np.zeros(6), "conf04": np.zeros(6),
         "rho": np.array([0.1, 0.5, 0.6, NAN, 2.0, 0.25])}
    thr = {"F": {"0.10": -3.0, "0.25": -2.0, "0.40": -1.0, "0.75": 0.0}, "P": {"0.10": -3.0, "0.25": -2.0, "0.40": -1.0, "0.75": 0.0}}
    by = {c["cid"]: c for c in r6.cell_specs()}
    m = r6.excl_mask(by["F25"], d, thr)
    assert m.tolist() == [True, False, False, False, False, False]                   # строго меньше порога; NaN проходит; значение = порогу проходит
    assert r6.excl_mask(by["F75r"], d, thr).tolist() == [False, False, False, False, False, True]   # обратная: строго выше q 0,75
    assert r6.excl_mask(by["C02"], d, thr).tolist() == [True, False, True, False, False, True]      # исключаем несмердженные; NaN проходит
    assert r6.excl_mask(by["Cr"], d, thr).tolist() == [False, True, False, False, True, False]
    assert r6.excl_mask(by["R05"], d, thr).tolist() == [True, True, False, False, False, True]      # ρ ≤ δ исключаем, ρ = δ — исключён
    assert r6.excl_mask(by["Rr"], d, thr).tolist() == [False, False, True, False, True, False]


def test_thresholds_linear_quantiles_defined_only():
    d = {"gF": np.array([NAN, 1.0, 2.0, 3.0, 4.0]), "gP": np.array([0.0, 0.0, 0.0, 0.0, 10.0])}
    t = r6.thresholds(d)
    assert close(t["F"]["0.25"], 1.75) and close(t["F"]["0.75"], 3.25) and t["P"]["0.40"] == 0.0 and t["P"]["0.75"] == 0.0


def test_admit_ex_ante_subset_rule():
    n = 100
    mon = np.repeat(np.arange(5), 20)
    mon[-12:] = 4                                           # ноябрь 12 сделок (допуск ≥ 8), остальные месяцы по 20 → 20, 20, 20, 20 − 12 ... упрощаем: считаем месяцы по факту
    mk = np.zeros(n, dtype=bool)
    ok, subset = r6.admit_ex_ante(mk, mon)                  # E = 0 < 30 → недопущена, не подмножество
    assert not ok and not subset
    mon2 = np.repeat(np.arange(5), [24, 24, 24, 24, 24])
    mk2 = np.zeros(120, dtype=bool)
    mk2[:36] = True                                         # исключено 36 ≥ 30; остаётся 84 из 120 (70 %): не подмножество
    ok2, sub2 = r6.admit_ex_ante(mk2, mon2)
    assert not sub2 and ok2 is False                        # месяц 0: остаётся 24 − 24 = 0 < 20 → недопущена
    mk3 = np.zeros(120, dtype=bool)
    mk3[np.arange(120) % 24 < 8] = True                      # по 8 в месяц (всего 40): остаётся 16 в месяц < 20 → недопущена, подмножество нет (67 %)
    ok3, sub3 = r6.admit_ex_ante(mk3, mon2)
    assert not sub3 and not ok3
    mon4 = np.repeat(np.arange(5), 40)                      # база 200: подмножество (остаётся 20 %): 8 в месяц = 40 ≥ 70? нет → недопущена
    mk4 = np.ones(200, dtype=bool)
    mk4[np.arange(200) % 40 < 8] = False
    ok4, sub4 = r6.admit_ex_ante(mk4, mon4)
    assert sub4 and not ok4
    mk5 = np.ones(200, dtype=bool)
    mk5[np.arange(200) % 40 < 16] = False                    # остаётся 16 в месяц, всего 80 ≥ 70, 16 ≥ 8; доля 40 % < 50 % → подмножество допущено
    ok5, sub5 = r6.admit_ex_ante(mk5, mon4)
    assert sub5 and ok5


def test_mant_bucket():
    b = r6.mant_bucket(np.array([1.0, 1.99, 2.0, 4.99, 5.0, 9.99, NAN]))
    assert b.tolist() == [0, 0, 1, 1, 2, 2, -1]


# ---- статистика ---------------------------------------------------------------------------------------------------------------------

def test_noise_bar_expected_max_abs_z():
    assert close(r6.expected_max_abs_z(1), 0.7978845608, 1e-5)         # E|Z| = √(2/π)
    assert 2.2 < r6.expected_max_abs_z(32) < 2.5 and r6.expected_max_abs_z(93) > r6.expected_max_abs_z(32)


def test_holm_family_61_plus_8():
    old = [0.5] * 61
    first = 0.05 / 69
    rej = st.holm_reject(old + [first * 0.99] + [0.9] * 7, 0.05)
    assert rej[61] and not any(rej[:61]) and not any(rej[62:])
    rej = st.holm_reject(old + [first * 1.01] + [0.9] * 7, 0.05)
    assert not rej[61]                                                   # чуть выше 0,05 / 69 — не проходит
    assert r6.N_OLD_TRIAL == 61 and len(r6.OLD_SELECT) == 4


def test_outcome_rule_and_admission_row():
    row = dict(admitted=False)
    assert r6.outcome_of(row, 0.5, {}).startswith("не ясно: клетка не допущена")
    ok = dict(admitted=True, g1=False, d=-0.1, **{"pass": False})
    assert r6.outcome_of(ok, 0.5, {}) == "не работает"
    ok = dict(admitted=True, g1=False, d=0.1, **{"pass": False})
    assert r6.outcome_of(ok, 0.5, {}).startswith("не ясно: |Δ| < MDE 0.50")
    ok = dict(admitted=True, g1=True, g2=True, g3=False, g4=True, d=0.1, **{"pass": False})
    assert r6.outcome_of(ok, 0.5, {}).startswith("качество сделок выше")
    ok = dict(admitted=True, g1=True, g2=True, g3=True, g4=True, g5=True, d=0.1, **{"pass": True})
    assert r6.outcome_of(ok, 0.5, {}).startswith("добавляет $")
    mt = {0: (0.1, 8, 20), 1: (0.1, 9, 20), 2: (0.1, 10, 20), 3: (0.1, 12, 20), 4: (0.1, 8, 8)}
    adm, sub = r6.admitted_666(dict(month=mt, n_c=47, n_r=120, a2=True))
    assert sub and not adm                                               # подмножество 47 < 70 за окно
    mt = {k: (0.1, 16, 20) for k in range(5)}
    mt[4] = (0.1, 8, 8)
    adm, sub = r6.admitted_666(dict(month=mt, n_c=72, n_r=108, a2=True))
    assert not sub and not adm                                           # не подмножество (67 %): клетка ≥ 20 в месяце не выполнена


def main():
    names = sorted(n for n in globals() if n.startswith("test_") and callable(globals()[n]))
    fail = 0
    for n in names:
        try:
            globals()[n]()
            print("ok  ", n)
        except Exception:
            fail += 1
            print("FAIL", n)
            traceback.print_exc()
    print("%d/%d" % (len(names) - fail, len(names)))
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
