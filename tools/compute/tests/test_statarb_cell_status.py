"""TK-493 w3 M-06/M-11 + w5 D10-D12: статус клетки (P-37 §10.4 с поправками), МРЭ, n_эфф, ряд «закрытые + MTM», условие (ж), два интервала."""
import importlib.util, math, os, random, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("cell_status_w3", os.path.join(HERE, "..", "statarb", "cell_status.py"))
cs = importlib.util.module_from_spec(spec)
sys.modules["cell_status_w3"] = cs
spec.loader.exec_module(cs)

# отрицательно скошенный ряд: 20 малых плюсов и один крупный минус (поиск по зёрнам, w5); блочный t даёт нижнюю границу > 0, бутстреп-t - нет
PESO = [0.68, 1.22, 1.07, 1.01, 1.21, 0.88, 1.5, 1.32, 1.09, 0.97, 0.9, 1.05, 0.89, 1.26, 1.16, -5.74, 1.12, 1.04, 1.08, 0.81, 0.76]


def noisy(mean, sd, n, seed):
    rng = random.Random(seed)
    return [rng.gauss(mean, sd) for _ in range(n)]


class Classify(unittest.TestCase):
    base = dict(n_trades=60, n_pairs=8, sigma_form=3.0, mean_without_best_coin=1.0, net_plus1=1.0)

    def test_candidate_when_cell_beats_control(self):
        cell = noisy(5.0, 1.0, 21, 1)
        ctrl = noisy(-1.0, 1.0, 21, 2)
        r = cs.classify(cell, ctrl, **self.base)
        self.assertEqual(r["status"], cs.CANDIDATE)
        self.assertTrue(all(r[k] for k in ("cond_a", "cond_b", "cond_c", "cond_d", "cond_e", "cond_g")))
        self.assertTrue(r["cond_a_t"] and r["cond_a_boot"] and r["cond_b_t"] and r["cond_b_boot"])
        self.assertGreater(r["diff_lo"], 0)
        self.assertGreater(r["diff_lo_boot"], 0)
        self.assertEqual(r["unmet"], [])

    def test_control_as_good_as_cell_blocks_candidate(self):
        # клетка положительна сама по себе, но так же, как случайные входы: «клетка − контроль» накрывает 0
        common = noisy(3.0, 2.0, 21, 3)
        cell = [c + e for c, e in zip(common, noisy(0.0, 0.3, 21, 4))]
        r = cs.classify(cell, common, **self.base)
        self.assertTrue(r["cond_a"])
        self.assertFalse(r["cond_b"])
        self.assertEqual(r["status"], cs.UNCLEAR)
        self.assertEqual(r["unmet"], ["b"])

    def test_no_control_is_not_candidate(self):
        r = cs.classify(noisy(5.0, 1.0, 21, 1), None, **self.base)
        self.assertFalse(r["cond_b"])
        self.assertIsNone(r["diff_lo"])
        self.assertEqual(r["status"], cs.UNCLEAR)

    def test_best_coin_filter_and_other_conditions(self):
        cell, ctrl = noisy(5.0, 1.0, 21, 1), noisy(-1.0, 1.0, 21, 2)
        for kw in (dict(mean_without_best_coin=-0.1), dict(mean_without_best_coin=None), dict(n_trades=29), dict(n_pairs=4),
                   dict(net_plus1=-0.1), dict(net_plus1=None), dict(net_plus1=0.0), dict(net_plus1=float("nan")), dict(mtm_ok=False)):
            args = dict(self.base)
            args.update(kw)
            self.assertEqual(cs.classify(cell, ctrl, **args)["status"], cs.UNCLEAR, kw)

    def test_plus1_condition_is_g_and_independent_of_the_rest(self):
        # D11: клетка проходит (а)-(д), но при +1 мин на обеих ногах оценка <= 0 (под-минутный фантом) - не кандидат, причина «g»
        cell, ctrl = noisy(5.0, 1.0, 21, 1), noisy(-1.0, 1.0, 21, 2)
        r = cs.classify(cell, ctrl, **dict(self.base, net_plus1=-8.18))
        self.assertEqual(r["unmet"], ["g"])
        self.assertFalse(r["cond_g"])
        self.assertTrue(r["cond_a"] and r["cond_b"] and r["cond_c"] and r["cond_d"] and r["cond_e"])
        self.assertEqual(r["status"], cs.UNCLEAR)
        self.assertEqual(r["net_plus1"], -8.18)

    def test_two_intervals_both_needed_for_a(self):
        # D12: отрицательно скошенный ряд («мелкие плюсы, один крупный минус»): блочный t оптимистичен, студентизованный бутстреп - нет
        r = cs.classify(PESO, None, **self.base)
        self.assertGreater(r["lo"], 0)
        self.assertLessEqual(r["lo_boot"], 0)
        self.assertTrue(r["cond_a_t"])
        self.assertFalse(r["cond_a_boot"])
        self.assertFalse(r["cond_a"])
        self.assertLess(r["skew"], -2.0)                  # скос печатается рядом со статусом
        ctrl = [-1.0] * len(PESO)
        r2 = cs.classify(PESO, ctrl, **self.base)
        self.assertNotEqual(r2["status"], cs.CANDIDATE)   # раньше (только t) был бы кандидат
        self.assertIn("a", r2["unmet"])

    def test_bootstrap_is_deterministic_and_seeded(self):
        cell, ctrl = noisy(2.0, 3.0, 21, 11), noisy(0.0, 3.0, 21, 12)
        a = cs.classify(cell, ctrl, **self.base)
        b = cs.classify(cell, ctrl, **self.base)
        self.assertEqual((a["lo_boot"], a["diff_lo_boot"]), (b["lo_boot"], b["diff_lo_boot"]))
        c = cs.classify(cell, ctrl, seed=38, **self.base)
        self.assertNotEqual(a["lo_boot"], c["lo_boot"])
        self.assertEqual(a["lo"], c["lo"])                 # блочный t от зерна не зависит

    def test_boot_on_diff_flag(self):
        # PESO в (б): разность с контролем -1 тоже скошена; с флагом boot_on_diff=False (б) считается только по блочному t
        ctrl = [-1.0] * len(PESO)
        on = cs.classify(PESO, ctrl, **self.base)
        off = cs.classify(PESO, ctrl, boot_on_diff=False, **self.base)
        self.assertIsNotNone(on["diff_lo_boot"])
        self.assertIsNone(off["diff_lo_boot"])
        self.assertTrue(off["cond_b"])

    def test_best_two_days_is_reference_only(self):
        cell = noisy(5.0, 1.0, 21, 1)
        r = cs.classify(cell, noisy(-1.0, 1.0, 21, 2), **self.base)
        want = sum(sorted(cell)[:-2]) / 19
        self.assertAlmostEqual(r["mean_without_best_days"], want, places=12)
        self.assertEqual(r["status"], cs.CANDIDATE)
        self.assertIsNone(cs.mean_without_best_days([1.0, 2.0]))

    def test_indistinguishable_needs_power(self):
        zero = noisy(0.0, 3.0, 21, 5)
        ctrl = noisy(0.0, 3.0, 21, 6)
        args = dict(self.base)
        r = cs.classify(zero, ctrl, **args)  # σ_форм 3,0 -> МРЭ = 2,8·3/√7 = 3,17 ≤ 12,6
        self.assertEqual(r["status"], cs.INDISTINGUISHABLE)
        args["sigma_form"] = 30.0            # МРЭ = 31,7 > 12,6 -> мощности нет
        self.assertEqual(cs.classify(zero, ctrl, **args)["status"], cs.UNCLEAR)
        args["sigma_form"] = None
        self.assertEqual(cs.classify(zero, ctrl, **args)["status"], cs.UNCLEAR)

    def test_invalid_sigma_form_is_not_power(self):
        # L5-06: σ_форм = 0, < 0, NaN, inf - «мощность» из пустого места; раньше МРЭ = 0 / отрицательный / NaN молча пропускались
        zero, ctrl = noisy(0.0, 3.0, 21, 5), noisy(0.0, 3.0, 21, 6)
        for bad in (0.0, -1.0, float("nan"), float("inf")):
            r = cs.classify(zero, ctrl, **dict(self.base, sigma_form=bad))
            self.assertEqual(r["status"], cs.UNCLEAR, bad)
            self.assertTrue(r["sigma_form_invalid"], bad)
            self.assertIsNone(r["mre"], bad)
        self.assertFalse(cs.classify(zero, ctrl, **self.base)["sigma_form_invalid"])
        self.assertFalse(cs.classify(zero, ctrl, **dict(self.base, sigma_form=None))["sigma_form_invalid"])

    def test_degenerate_and_short_series_never_indistinguishable_or_candidate(self):
        # L5-06: константный положительный ряд (var = 0) раньше давал «кандидат» с p = 0; 1-2 суток (df = 0) - «не различимо»
        r = cs.classify([5.0] * 21, [0.0] * 21, **self.base)
        self.assertTrue(r["degenerate"])
        self.assertEqual(r["status"], cs.UNCLEAR)
        for n in (1, 2):
            rr = cs.classify([0.5] * n, None, **dict(self.base, sigma_form=1.0))
            self.assertEqual(rr["status"], cs.UNCLEAR, n)
        self.assertEqual(cs.classify([0.0] * 21, [0.0] * 21, **self.base)["status"], cs.UNCLEAR)   # все нули: интервал схлопнут

    def test_nonfinite_series_is_unclear_without_crash(self):
        for bad in (float("nan"), float("inf")):
            cell = noisy(5.0, 1.0, 21, 1)
            cell[3] = bad
            r = cs.classify(cell, noisy(-1.0, 1.0, 21, 2), **self.base)
            self.assertEqual(r["status"], cs.UNCLEAR)
            self.assertTrue(r["nonfinite_series"])
        ctrl = noisy(-1.0, 1.0, 21, 2)
        ctrl[0] = float("nan")
        self.assertEqual(cs.classify(noisy(5.0, 1.0, 21, 1), ctrl, **self.base)["status"], cs.UNCLEAR)

    def test_indist_min_trades_flag(self):
        zero, ctrl = noisy(0.0, 3.0, 21, 5), noisy(0.0, 3.0, 21, 6)
        few = dict(self.base, n_trades=5)
        self.assertEqual(cs.classify(zero, ctrl, **few)["status"], cs.INDISTINGUISHABLE)                       # протокол: N >= 1 хватает
        self.assertEqual(cs.classify(zero, ctrl, indist_min_trades=cs.MIN_TRADES, **few)["status"], cs.UNCLEAR)  # L5-06: как для кандидата
        self.assertEqual(cs.classify(zero, ctrl, indist_min_trades=cs.MIN_TRADES, **self.base)["status"], cs.INDISTINGUISHABLE)

    def test_minus_label(self):
        r = cs.classify(noisy(-6.0, 1.0, 21, 7), noisy(0.0, 1.0, 21, 8), **self.base)
        self.assertTrue(r["minus"])
        self.assertEqual(r["status"], cs.UNCLEAR)

    def test_empty_cell_unclear_by_default_literal_protocol_on_flag(self):
        zeros = [0.0] * 21
        args = dict(self.base, n_trades=0, n_pairs=0)
        self.assertEqual(cs.classify(zeros, zeros, **args)["status"], cs.UNCLEAR)
        # буквальный протокол (интервал [0, 0], МРЭ = 0): флаг возвращает «не различимо» только если интервал не схлопнут; w5 (L5-06) схлопнутый интервал
        # не считает мощностью, поэтому и с флагом все нули остаются «не ясно»
        self.assertEqual(cs.classify(zeros, zeros, empty_unclear=False, **args)["status"], cs.UNCLEAR)
        noise = noisy(0.0, 3.0, 21, 5)
        self.assertEqual(cs.classify(noise, noise, empty_unclear=False, **args)["status"], cs.INDISTINGUISHABLE)
        self.assertEqual(cs.classify(noise, noise, **args)["status"], cs.UNCLEAR)

    def test_fee_burden_is_reference_only(self):
        zero, ctrl = noisy(0.0, 3.0, 21, 5), noisy(0.0, 3.0, 21, 6)
        r0 = cs.classify(zero, ctrl, **self.base)
        r1 = cs.classify(zero, ctrl, fee_burden_bps=2.0, **self.base)
        self.assertEqual(r0["status"], r1["status"])
        self.assertIsNone(r0["mre_over_fee_burden"])
        self.assertAlmostEqual(r1["mre_over_fee_burden"], r1["mre"] / 2.0, places=12)

    def test_mre_reference_fields(self):
        # M-11: согласованный множитель 2,487 и σ торгового окна - справка, статус не меняют
        zero, ctrl = noisy(0.0, 3.0, 21, 5), noisy(0.0, 3.0, 21, 6)
        r = cs.classify(zero, ctrl, **self.base)
        self.assertAlmostEqual(r["mre_consistent"], r["mre"] * cs.mre_coef_for() / cs.MRE_COEF, places=12)
        self.assertGreater(r["sigma_window"], 0)
        self.assertAlmostEqual(r["mre_window"], cs.mre(r["sigma_window"], r["n_eff"]), places=12)
        self.assertIsNone(cs.classify(zero, ctrl, **dict(self.base, sigma_form=None))["mre_consistent"])

    def test_closed_variant_is_printed_beside(self):
        closed = noisy(5.0, 1.0, 21, 1)
        primary = list(closed)
        primary[-1] -= 40.0                       # MTM открытых позиций на последних сутках
        r = cs.classify(primary, None, closed_days=closed, **self.base)
        self.assertGreater(r["closed_lo"], 0)
        self.assertLess(r["mean"], r["closed_mean"])
        self.assertIsNone(cs.classify(primary, None, **self.base)["closed_mean"])

    def test_worst_trade_reference(self):
        # L5-02: нижняя граница без худшей сделки (peso problem): вклад -5,74 на сутки 15 снят - ряд без выброса, граница выше
        r = cs.classify(PESO, None, worst_trade=(15, -5.74 - 1.0), **self.base)
        self.assertGreater(r["lo_without_worst_trade"], r["lo"])
        self.assertEqual(r["worst_trade_value"], -6.74)
        self.assertIsNone(cs.classify(PESO, None, **self.base)["lo_without_worst_trade"])
        self.assertIsNone(cs.classify(PESO, None, worst_trade=(99, -1.0), **self.base)["lo_without_worst_trade"])

    def test_length_mismatch(self):
        with self.assertRaises(ValueError):
            cs.classify([1.0] * 21, [0.0] * 20, **self.base)
        with self.assertRaises(ValueError):
            cs.classify([], None, **self.base)

    def test_open_semantics_and_short_share(self):
        self.assertEqual(cs.open_semantics(["bybit", "bitget"]), "chain")
        self.assertEqual(cs.open_semantics(["Binance", "okx"]), "first_trade")
        self.assertEqual(cs.open_semantics(["binance", "bybit"]), "mixed")
        self.assertEqual(cs.open_semantics(["binance", "hyperliquid"]), "unknown")
        self.assertEqual(cs.open_semantics([]), "unknown")
        for ex in ("bybit", "bingx", "bitget", "kraken", "lighter"):
            self.assertEqual(cs.OPEN_SEMANTICS[ex], "chain")
        for ex in ("binance", "okx", "aster"):
            self.assertEqual(cs.OPEN_SEMANTICS[ex], "first_trade")
        self.assertEqual(cs.short_trade_share([1, 2, 3, 2, 10]), 0.6)
        self.assertIsNone(cs.short_trade_share([]))
        r = cs.classify(noisy(5.0, 1.0, 21, 1), None, exchanges=["bingx"], short_trade_share=0.43, **self.base)
        self.assertEqual((r["open_semantics"], r["short_trade_share"]), ("chain", 0.43))


class BuildSeries(unittest.TestCase):
    days = [10, 11, 12, 13]
    net = {10: 20.0, 11: -10.0, 12: 0.0, 13: 6.0}
    act = {10: 2, 11: 2, 12: 0, 13: 3}

    def test_closed_only_skips_inactive_days(self):
        s = cs.build_series(self.net, self.act, self.days)
        self.assertEqual(s["days"], [10, 11, 13])
        self.assertEqual(s["closed"], [10.0, -5.0, 2.0])
        self.assertFalse(s["mtm_available"])
        self.assertIsNone(s["primary"])

    def test_mtm_goes_to_last_day_over_active_pairs(self):
        s = cs.build_series(self.net, self.act, self.days, mtm_net_sum=-30.0, n_open=4)
        self.assertTrue(s["mtm_available"])
        self.assertEqual(s["mtm_denominator"], 3)
        self.assertAlmostEqual(s["mtm_value"], -10.0)
        self.assertEqual(s["primary_days"], [10, 11, 13])
        self.assertEqual(s["primary"], [10.0, -5.0, 2.0 - 10.0])
        self.assertFalse(s["mtm_day_added"])
        self.assertEqual(s["closed"], [10.0, -5.0, 2.0])           # закрытый ряд не тронут

    def test_zero_mtm_and_no_open_positions(self):
        s = cs.build_series(self.net, self.act, self.days, mtm_net_sum=0.0, n_open=0)
        self.assertTrue(s["mtm_available"])
        self.assertEqual(s["primary"], s["closed"])
        self.assertIsNone(s["mtm_denominator"])

    def test_nan_mtm_is_unavailable(self):
        for bad in (None, float("nan"), float("inf")):
            self.assertFalse(cs.build_series(self.net, self.act, self.days, mtm_net_sum=bad)["mtm_available"], bad)

    def test_last_day_without_active_pairs_adds_day_with_open_positions_denominator(self):
        act = dict(self.act)
        act[13] = 0
        s = cs.build_series(self.net, act, self.days, mtm_net_sum=-30.0, n_open=5)
        self.assertTrue(s["mtm_day_added"])
        self.assertEqual(s["primary_days"], [10, 11, 13])
        self.assertAlmostEqual(s["primary"][-1], -6.0)                # сделки суток без активных пар (6.0) в ряд не входят
        with self.assertRaises(ValueError):
            cs.build_series(self.net, act, self.days, mtm_net_sum=-30.0, n_open=0)

    def test_control_aligned_to_primary_days(self):
        ctrl = {10: 2.0, 11: 0.0, 12: 99.0, 13: -3.0}
        s = cs.build_series(self.net, self.act, self.days, mtm_net_sum=-30.0, n_open=4, control_net=ctrl)
        self.assertEqual(s["control"], [1.0, 0.0, -1.0])               # знаменатель - активные пары, MTM у контроля нет
        self.assertEqual(s["control_closed"], [1.0, 0.0, -1.0])
        s0 = cs.build_series(self.net, self.act, self.days, control_net=ctrl)
        self.assertIsNone(s0["control"])
        self.assertEqual(s0["control_closed"], [1.0, 0.0, -1.0])
        with self.assertRaises(ValueError):
            cs.build_series(self.net, self.act, [])

    def test_mtm_can_turn_closed_candidate_into_not(self):
        # L6-03: закрытые сделки выглядят хорошо, а нерешённые убыточные позиции на конце окна съедают результат
        days = list(range(21))
        rng = random.Random(5)
        net = {d: 10.0 + rng.gauss(0, 2.0) for d in days}
        act = {d: 1 for d in days}
        base = dict(n_trades=60, n_pairs=8, sigma_form=3.0, mean_without_best_coin=1.0, net_plus1=1.0)
        ctrl = {d: -1.0 for d in days}
        s = cs.build_series(net, act, days, mtm_net_sum=-250.0, n_open=3, control_net=ctrl)
        closed_status = cs.classify(s["closed"], s["control_closed"], **base)["status"]
        mtm_status = cs.classify(s["primary"], s["control"], mtm_ok=s["mtm_available"], closed_days=s["closed"], **base)
        self.assertEqual(closed_status, cs.CANDIDATE)
        self.assertNotEqual(mtm_status["status"], cs.CANDIDATE)
        self.assertGreater(mtm_status["closed_lo"], 0)
        # без MTM основной ряд кандидатом быть не может, даже если закрытые сделки идеальны
        s2 = cs.build_series(net, act, days, control_net=ctrl)
        self.assertNotEqual(cs.classify(s2["closed"], s2["control_closed"], mtm_ok=s2["mtm_available"], **base)["status"], cs.CANDIDATE)


class GateAndRow(unittest.TestCase):
    def test_gate_means_count_open_positions(self):
        # 100 закрытых: валовый 40 и комиссия 13 на сделку; 10 открытых на конце окна с MTM: валовый -300 и комиссия 13 на позицию
        g, f, n = cs.gate_position_means(4000.0, 1300.0, 100, -3000.0, 130.0, 10)
        self.assertEqual(n, 110)
        self.assertAlmostEqual(g, (4000.0 - 3000.0) / 110)
        self.assertAlmostEqual(f, (1300.0 + 130.0) / 110)
        self.assertLess(g, 2 * f)                       # закрытый вариант (40 >= 26) ворота прошёл бы, вариант с MTM - нет

    def test_gate_means_without_mtm_are_refused_not_closed_only(self):
        for bad in (None, float("nan")):
            g, f, n = cs.gate_position_means(4000.0, 1300.0, 100, bad, bad, 0)
            self.assertTrue(math.isnan(g) and math.isnan(f))
            self.assertEqual(n, 100)
        self.assertEqual(cs.gate_position_means(0.0, 0.0, 0, 0.0, 0.0, 0)[2], 0)

    def test_status_row_matches_columns_and_serialises(self):
        cell, ctrl = noisy(5.0, 1.0, 21, 1), noisy(-1.0, 1.0, 21, 2)
        r = cs.classify(cell, ctrl, n_trades=60, n_pairs=8, sigma_form=3.0, mean_without_best_coin=1.0, net_plus1=-2.0,
                        exchanges=["bingx"], short_trade_share=0.4, closed_days=cell)
        row = cs.status_row("A1|bingx|eq_us|a0", r)
        self.assertEqual(len(row), len(cs.STATUS_COLUMNS))
        d = dict(zip(cs.STATUS_COLUMNS, row))
        self.assertEqual((d["cell"], d["status"], d["cond_g"], d["unmet"], d["open_semantics"]), ("A1|bingx|eq_us|a0", "unclear", 0, "g", "chain"))
        self.assertEqual(d["short_trade_share_le_2min"], 0.4)
        self.assertEqual(d["lo_boot_t"], r["lo_boot"])
        self.assertEqual(len(set(cs.STATUS_COLUMNS)), len(cs.STATUS_COLUMNS))


class Mre(unittest.TestCase):
    def test_protocol_values(self):
        self.assertEqual(cs.n_eff_blocks(21), 7)
        self.assertEqual(cs.n_eff_blocks(15), 5)
        self.assertAlmostEqual(cs.mre(1.0, 7), 1.0583, places=4)   # P-37 §10.3: ≈ 1,06σ
        self.assertAlmostEqual(cs.mre(1.0, 5), 1.2522, places=4)   # ≈ 1,25σ

    def test_coef_consistent_with_interval(self):
        # 2,8 = z(0,975) + z(0,80) - двусторонний 5 %; правило «нижняя граница 90 %-интервала > 0» - односторонний 5 % -> 2,487
        self.assertAlmostEqual(cs.mre_coef_for(0.05, 0.80), 2.8016, places=3)
        self.assertAlmostEqual(cs.mre_coef_for(0.10, 0.80), 2.4865, places=3)

    def test_expected_false(self):
        self.assertAlmostEqual(cs.expected_false_candidates(500), 25.0)
        self.assertAlmostEqual(cs.expected_false_candidates(500, 0.045), 22.5)

    def test_expected_false_range_and_distinct_cells(self):
        lo, hi = cs.expected_false_range(500, 40)
        self.assertAlmostEqual(lo, 2.0)
        self.assertAlmostEqual(hi, 25.0)
        self.assertEqual(cs.expected_false_range(10, 40), (0.5, 2.0))       # M_эфф > M не растит нижний конец выше верхнего
        series = {"A1|x|a0": [1.0, 2.0, 3.0], "A1|x|a1": [1.0, 2.0, 3.0 + 1e-12], "A1|y|a0": [1.0, 2.0, 4.0]}
        self.assertEqual(cs.m_distinct(series), 2)                          # a1, тождественная a0, считается один раз

    def test_m_eff_participation_ratio(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("нет numpy (запускать на Deck)")
        rng = np.random.default_rng(1)
        t = 4000
        indep = rng.normal(size=(t, 20))
        v, m = cs.m_eff(indep)
        self.assertEqual(m, 20)
        self.assertAlmostEqual(v, 20.0, delta=1.0)                           # независимые ряды: M_эфф ≈ M
        one = rng.normal(size=(t, 1))
        copies = np.hstack([one + 1e-3 * rng.normal(size=(t, 1)) for _ in range(20)])
        v2, _ = cs.m_eff(copies)
        self.assertAlmostEqual(v2, 1.0, delta=0.05)                          # почти копии: M_эфф ≈ 1
        const = np.hstack([indep[:, :3], np.zeros((t, 1))])
        self.assertEqual(cs.m_eff(const)[1], 3)                              # столбец с нулевой дисперсией отброшен


if __name__ == "__main__":
    unittest.main()
