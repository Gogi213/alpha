"""Юнит-тесты П-42 TK-602 (tk602_shape.py): серии знаков, плато и просадка, R² прямой, доли лучших суток, k80, меры на синтетике.
Запуск на srv: cd /data/p42-tk602/py && /opt/venv/bin/python -m unittest test_tk602 (numpy, pandas, scipy)."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk602_shape as S  # noqa: E402


class ShapeTests(unittest.TestCase):
    def test_runs(self):
        r = S.runs_stats([1, 2, -1, 3, -1, -2, 0])
        self.assertEqual((r["n_runs"], r["longest_pos"], r["longest_neg"], r["n_pos"], r["n_neg"]), (4, 2, 2, 3, 3))
        self.assertEqual(S.runs_stats([0, 0])["n_runs"], 0)

    def test_plateau_and_drawdown(self):
        p = S.plateau_stats([1, 2, 2, 1, 3])
        self.assertEqual((p["nonew_days"], p["longest_plateau"], p["maxdd"], p["maxdd_days"]), (2, 2, 1.0, 2))
        q = S.plateau_stats([-1, -2, -3])          # эквити ниже старта: нового максимума нет, просадка от 0 до −3, возврата нет
        self.assertEqual((q["nonew_days"], q["longest_plateau"], q["maxdd"], q["maxdd_days"]), (3, 3, 3.0, 4))

    def test_r2(self):
        r2, sl = S.r2_line([1, 2, 3, 4])
        self.assertAlmostEqual(r2, 1.0)
        self.assertAlmostEqual(sl, 1.0)
        r2s, _ = S.r2_line([0, 0, 0, 10, 10, 10])   # ступень — R² ниже единицы
        self.assertLess(r2s, 0.9)

    def test_top_share_and_k80(self):
        s, sh, shp = S.top_share([5, 3, 1, 1], 1)
        self.assertEqual((s, sh), (5.0, 0.5))
        self.assertAlmostEqual(shp, 0.5)
        self.assertIsNone(S.top_share([5, -6], 1)[1])    # сумма ≤ 0 — доли нет
        self.assertEqual(S.k80([10, 10, 10, 10, 10, -5]), 4)
        self.assertEqual(S.k80([-1, -2]), 0)

    def test_measures_step_vs_smooth(self):
        rng = np.random.default_rng(1)
        n_days = 20
        # ступенька: все 100 $ за двое суток; плавно: по 10 $ каждые сутки
        step = S.measures([50.0, 50.0], np.array([3, 14]), n_days, rng, with_null=False)[0]
        smooth = S.measures([10.0] * n_days, np.arange(n_days), n_days, rng, with_null=False)[0]
        self.assertAlmostEqual(step["top1d_share"], 0.5)
        self.assertAlmostEqual(step["top3d_share"], 1.0)
        self.assertEqual(smooth["top5d_share"], 0.25)
        self.assertAlmostEqual(smooth["r2"], 1.0)
        self.assertLess(step["r2"], smooth["r2"])
        self.assertEqual(step["days_no_trades"], 18)
        self.assertEqual(smooth["longest_plateau"], 0)
        self.assertEqual(step["longest_plateau"], 10)      # сутки 4…13 без нового максимума

    def test_measures_totals_match(self):
        rng = np.random.default_rng(2)
        net = rng.normal(0.3, 2.0, 120)
        dc = rng.integers(0, 30, 120)
        m, d, cnt = S.measures(net, dc, 30, rng, with_null=False)
        self.assertAlmostEqual(d.sum(), net.sum())
        self.assertEqual(int(cnt.sum()), 120)
        self.assertAlmostEqual(m["total"], net.sum())


if __name__ == "__main__":
    unittest.main()
