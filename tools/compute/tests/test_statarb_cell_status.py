"""TK-493 w3 M-06/M-11: статус клетки (P-37 §10.4 с поправками), МРЭ, n_эфф."""
import importlib.util, os, random, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("cell_status_w3", os.path.join(HERE, "..", "statarb", "cell_status.py"))
cs = importlib.util.module_from_spec(spec)
sys.modules["cell_status_w3"] = cs
spec.loader.exec_module(cs)


def noisy(mean, sd, n, seed):
    rng = random.Random(seed)
    return [rng.gauss(mean, sd) for _ in range(n)]


class Classify(unittest.TestCase):
    base = dict(n_trades=60, n_pairs=8, mean_mtm=1.0, sigma_form=3.0, mean_without_best_coin=1.0)

    def test_candidate_when_cell_beats_control(self):
        cell = noisy(5.0, 1.0, 21, 1)
        ctrl = noisy(-1.0, 1.0, 21, 2)
        r = cs.classify(cell, ctrl, **self.base)
        self.assertEqual(r["status"], cs.CANDIDATE)
        self.assertTrue(r["cond_a"] and r["cond_b"] and r["cond_c"] and r["cond_d"] and r["cond_e"])
        self.assertGreater(r["diff_lo"], 0)

    def test_control_as_good_as_cell_blocks_candidate(self):
        # клетка положительна сама по себе, но так же, как случайные входы: «клетка − контроль» накрывает 0
        common = noisy(3.0, 2.0, 21, 3)
        cell = [c + e for c, e in zip(common, noisy(0.0, 0.3, 21, 4))]
        r = cs.classify(cell, common, **self.base)
        self.assertTrue(r["cond_a"])
        self.assertFalse(r["cond_b"])
        self.assertEqual(r["status"], cs.UNCLEAR)

    def test_no_control_is_not_candidate(self):
        r = cs.classify(noisy(5.0, 1.0, 21, 1), None, **self.base)
        self.assertFalse(r["cond_b"])
        self.assertIsNone(r["diff_lo"])
        self.assertEqual(r["status"], cs.UNCLEAR)

    def test_best_coin_filter_and_other_conditions(self):
        cell, ctrl = noisy(5.0, 1.0, 21, 1), noisy(-1.0, 1.0, 21, 2)
        for kw in (dict(mean_without_best_coin=-0.1), dict(mean_without_best_coin=None), dict(n_trades=29), dict(n_pairs=4),
                   dict(mean_mtm=-0.1), dict(mean_mtm=None)):
            args = dict(self.base)
            args.update(kw)
            self.assertEqual(cs.classify(cell, ctrl, **args)["status"], cs.UNCLEAR, kw)

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

    def test_minus_label(self):
        r = cs.classify(noisy(-6.0, 1.0, 21, 7), noisy(0.0, 1.0, 21, 8), **self.base)
        self.assertTrue(r["minus"])
        self.assertEqual(r["status"], cs.UNCLEAR)

    def test_empty_cell_unclear_by_default_literal_protocol_on_flag(self):
        zeros = [0.0] * 21
        args = dict(self.base, n_trades=0, n_pairs=0)
        self.assertEqual(cs.classify(zeros, zeros, **args)["status"], cs.UNCLEAR)
        self.assertEqual(cs.classify(zeros, zeros, empty_unclear=False, **args)["status"], cs.INDISTINGUISHABLE)

    def test_fee_burden_is_reference_only(self):
        zero, ctrl = noisy(0.0, 3.0, 21, 5), noisy(0.0, 3.0, 21, 6)
        r0 = cs.classify(zero, ctrl, **self.base)
        r1 = cs.classify(zero, ctrl, fee_burden_bps=2.0, **self.base)
        self.assertEqual(r0["status"], r1["status"])
        self.assertIsNone(r0["mre_over_fee_burden"])
        self.assertAlmostEqual(r1["mre_over_fee_burden"], r1["mre"] / 2.0, places=12)

    def test_length_mismatch(self):
        with self.assertRaises(ValueError):
            cs.classify([1.0] * 21, [0.0] * 20, **self.base)
        with self.assertRaises(ValueError):
            cs.classify([], None, **self.base)


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


if __name__ == "__main__":
    unittest.main()
