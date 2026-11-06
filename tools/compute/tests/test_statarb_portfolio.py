"""TK-484: портфельный слой П-32 (statarb/portfolio.py) — сутки закрытия, бутстреп, Холм, KPI, плацебо."""
import importlib.util, math, os, random, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("portfolio", os.path.join(HERE, "..", "statarb", "portfolio.py"))
p = importlib.util.module_from_spec(spec)
sys.modules["portfolio"] = p
spec.loader.exec_module(p)


class Daily(unittest.TestCase):
    def test_closing_day_sum_and_count(self):
        d = p.daily_from_trades([(10, 5.0), (1439, -2.0), (1440, 7.0), (3000, 1.5)])
        self.assertEqual(d, {0: (3.0, 2), 1: (7.0, 1), 2: (1.5, 1)})
        self.assertEqual(p.daily_series(d, 0, 3), [3.0, 7.0, 1.5, 0.0])


class Bootstrap(unittest.TestCase):
    def test_block_len(self):
        self.assertEqual([p.block_len(n) for n in (1, 8, 9, 27, 1000)], [1, 2, 3, 3, 10])

    def test_positive_series_excludes_zero_and_zero_series_covers(self):
        pos = [1.0 + 0.1 * ((i * 7) % 5 - 2) for i in range(200)]
        mean, lo, hi, pv = p.block_bootstrap(pos, 500, 1, 0.05)
        self.assertGreater(lo, 0)
        self.assertLess(pv, 0.05)
        noise = [(-1.0) ** i * (1 + (i % 3)) for i in range(200)]
        mean, lo, hi, pv = p.block_bootstrap(noise, 500, 1, 0.05)
        self.assertLessEqual(lo, 0.0)
        self.assertGreaterEqual(hi, 0.0)

    def test_same_seed_same_result(self):
        s = [float((i * 13) % 7 - 3) for i in range(100)]
        self.assertEqual(p.block_bootstrap(s, 200, 5, 0.1), p.block_bootstrap(s, 200, 5, 0.1))


class TInterval(unittest.TestCase):
    """w3 M-04: t-интервал по блокам, df = n_эфф−1, и квантили Стьюдента на stdlib."""

    def test_t_ppf_table_values(self):
        # справочные значения квантилей Стьюдента (одностороннее 0,95 / 0,975)
        for p_, df, want in ((0.95, 1, 6.313752), (0.95, 4, 2.131847), (0.95, 6, 1.943180), (0.975, 20, 2.085963)):
            self.assertAlmostEqual(p.t_ppf(p_, df), want, places=5)
        self.assertAlmostEqual(p.t_ppf(0.05, 6), -1.943180, places=5)
        self.assertEqual(p.t_ppf(0.5, 3), 0.0)
        with self.assertRaises(ValueError):
            p.t_ppf(1.0, 3)

    def test_t_ppf_against_scipy(self):
        try:
            from scipy import stats
        except ImportError:
            self.skipTest("scipy нет")
        for df in (1, 2, 3, 4, 6, 10, 30):
            for q in (0.6, 0.9, 0.95, 0.975, 0.995):
                self.assertAlmostEqual(p.t_ppf(q, df) / stats.t.ppf(q, df), 1.0, places=9)
        self.assertAlmostEqual(p.t_sf_two_sided(2.0, 6), 2 * stats.t.sf(2.0, 6), places=12)

    def test_equal_blocks_match_batch_means_formula(self):
        x = [float((i * 7) % 11 - 4) for i in range(21)]
        mean, lo, hi, pv, df = p.block_t_interval(x, 0.10, 3)
        bm = [sum(x[k:k + 3]) / 3 for k in range(0, 21, 3)]
        m = sum(bm) / 7
        sd = (sum((b - m) ** 2 for b in bm) / 6) ** 0.5
        se = sd / 7 ** 0.5
        q = p.t_ppf(0.95, 6)
        self.assertEqual(df, 6)
        self.assertAlmostEqual(mean, m, places=12)
        self.assertAlmostEqual(lo, m - q * se, places=10)
        self.assertAlmostEqual(hi, m + q * se, places=10)

    def test_uneven_last_block_cluster_formula(self):
        x = [1.0, -2.0, 3.0, 0.5, 0.5, -1.0, 2.0, 4.0, -3.0, 1.0]  # n = 10, блок 3 -> блоки 3,3,3,1; n_эфф = 4
        mean, lo, hi, pv, df = p.block_t_interval(x, 0.10, 3)
        self.assertEqual(df, 3)
        segs = [x[0:3], x[3:6], x[6:9], x[9:10]]
        ss = sum((sum(s) - mean * len(s)) ** 2 for s in segs)
        se = (4 / 3 * ss / 100) ** 0.5
        self.assertAlmostEqual(hi - mean, p.t_ppf(0.95, 3) * se, places=10)

    def test_p_value_consistent_with_interval(self):
        for seed in range(30):
            rng = random.Random(seed)
            x = [rng.gauss(0.4, 1.0) for _ in range(21)]
            mean, lo, hi, pv, df = p.block_t_interval(x, 0.10)
            self.assertEqual(pv < 0.10, lo > 0 or hi < 0)

    def test_degenerate_cases(self):
        self.assertEqual(p.block_t_interval([0.0] * 21, 0.10), (0.0, 0.0, 0.0, 1.0, 6))
        mean, lo, hi, pv, df = p.block_t_interval([2.0] * 21, 0.10)
        self.assertEqual((mean, lo, hi, pv), (2.0, 2.0, 2.0, 0.0))
        mean, lo, hi, pv, df = p.block_t_interval([1.0, 2.0], 0.10)  # n = 2 -> один блок (блок ⌈2^{1/3}⌉ = 2)
        self.assertEqual((lo, hi, pv, df), (-math.inf, math.inf, 1.0, 0))
        with self.assertRaises(ValueError):
            p.block_t_interval([], 0.10)

    def test_no_randomness_and_default_block(self):
        x = [float((i * 13) % 7 - 3) for i in range(21)]
        self.assertEqual(p.block_t_interval(x, 0.10), p.block_t_interval(x, 0.10, 3))


class HolmAndPlacebo(unittest.TestCase):
    def test_holm(self):
        self.assertEqual(p.holm([0.01, 0.04, 0.03]), [0.03, 0.06, 0.06])
        self.assertEqual(p.holm([0.5]), [0.5])

    def test_placebo_unique_and_avoids_selected(self):
        syms = [f"S{i}" for i in range(10)]
        out = p.placebo_pairs(syms, 20, 3, forbidden=[("S0", "S1")])
        self.assertEqual(len(out), 20)
        self.assertEqual(len({frozenset(x) for x in out}), 20)
        self.assertNotIn(frozenset(("S0", "S1")), {frozenset(x) for x in out})
        with self.assertRaises(ValueError):
            p.placebo_pairs(syms, 45, 3, forbidden=[("S0", "S1")])


class Kpi(unittest.TestCase):
    def test_days_to_new_high(self):
        # кривая: +1, -1, +1 (0 — не новый максимум), +2 (новый), нули → хвост
        periods, tail, n_high, p90 = p.days_to_new_high([1.0, -1.0, 1.0, 2.0, 0.0, 0.0])
        self.assertEqual(n_high, 2)
        self.assertEqual(periods, [1, 3])
        self.assertEqual(tail, 6 - 4)
        self.assertIsNotNone(p90)


if __name__ == "__main__":
    unittest.main()
