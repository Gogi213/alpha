"""TK-484: портфельный слой П-32 (statarb/portfolio.py) — сутки закрытия, бутстреп, Холм, KPI, плацебо."""
import importlib.util, os, sys, unittest

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
