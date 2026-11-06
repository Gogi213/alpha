"""TK-493 w3 M-04: тест размера интервала по суткам на синтетическом нуле (numpy).

Доля случаев «нижняя граница 90 %-интервала > 0» при среднем 0 должна быть ≈ 5 %: критерий задания - не больше 6 %.
Проверяется боевая функция `portfolio.block_t_interval` (не копия). Перцентильный бутстреп (прежний способ) измерен в size_test.py
и записан в docs/findings/xdl9-stats-fixes-2026-11-06-size.csv (8,0-10,3 % на независимых сутках): здесь только согласованность
его векторной копии с `portfolio.block_bootstrap`, чтобы цифры файла относились к тому, что считает боевой код.
"""
import importlib.util, math, os, sys, unittest
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("size_test_w3", os.path.join(HERE, "..", "statarb", "size_test.py"))
st = importlib.util.module_from_spec(spec)
sys.modules["size_test_w3"] = st
spec.loader.exec_module(st)

REPS = 2000  # не меньше 2000 (задание); SE доли при 5 % ≈ 0,5 п.п.


class Size(unittest.TestCase):
    def _size(self, scen, n):
        xs = st.gen(np.random.default_rng(37), scen, REPS, n)
        lo = np.array([st.bounds_t_block(xs[i])[0] for i in range(REPS)])
        hi = np.array([st.bounds_t_block(xs[i])[1] for i in range(REPS)])
        return float((lo > 0).mean()), float((hi < 0).mean())

    def test_independent_days_size_not_above_6pct(self):
        for scen in ("normal", "t3", "sparse"):
            for n in (15, 21):
                lo_gt0, hi_lt0 = self._size(scen, n)
                self.assertLessEqual(lo_gt0, 0.06, (scen, n, lo_gt0))
                self.assertLessEqual(hi_lt0, 0.06, (scen, n, hi_lt0))

    def test_normal_days_not_overconservative(self):
        for n in (15, 21):
            lo_gt0, _ = self._size("normal", n)
            self.assertGreaterEqual(lo_gt0, 0.035, (n, lo_gt0))

    def test_vectorised_percentile_matches_stdlib_block_bootstrap(self):
        P = st.P
        x = [float((i * 7) % 11 - 4) + 0.3 for i in range(21)]
        _, lo_s, hi_s, _ = P.block_bootstrap(x, 3000, 37, 0.10)
        (lo_v, hi_v), _ = st.bounds_bootstrap(np.array(x), 3000, np.random.default_rng(37))
        sd_mean = float(np.std(x, ddof=1)) / 21 ** 0.5
        self.assertLess(abs(lo_s - lo_v), 0.25 * sd_mean + 1e-9)
        self.assertLess(abs(hi_s - hi_v), 0.25 * sd_mean + 1e-9)


class BootTAndSkew(unittest.TestCase):
    """w5 D12 (L5-02, L1-02): студентизованный бутстреп-t как второй интервал статуса и скошенный нуль в тесте размера."""

    def test_vectorised_boot_t_equals_stdlib_with_same_starts(self):
        P = st.P
        for n in (21, 15, 22):                              # 22 - усечённый последний блок
            x = np.random.default_rng(n).normal(0.3, 2.0, size=n)
            b, B = 3, 500
            starts = P.boot_t_starts(n, b, B, 11)
            _, lo_s, hi_s = P.block_boot_t_interval(list(map(float, x)), 0.10, B, 11, b, starts=starts)
            nb = math.ceil(n / b)
            idx = ((np.array(starts)[:, :, None] + np.arange(b)) % n).reshape(B, nb * b)[:, :n]
            lo_v, hi_v = st.boot_t_from_resamples(x, x[idx])
            self.assertAlmostEqual(lo_s, float(lo_v), places=9)
            self.assertAlmostEqual(hi_s, float(hi_v), places=9)

    def test_both_is_never_looser_than_either_interval(self):
        xs = st.gen(np.random.default_rng(1), "normal", 200, 21)
        res = st.run_series(xs, 200, 5, methods=("t_block", "boot_t", "both"))
        t_gt, b_gt, both_gt = (res[m][0] > 0 for m in ("t_block", "boot_t", "both"))
        self.assertTrue(np.array_equal(both_gt, t_gt & b_gt))
        lo_t, hi_t = res["t_block"]
        lo_b, hi_b = res["boot_t"]
        self.assertTrue(np.allclose(res["both"][0], np.minimum(lo_t, lo_b)))
        self.assertTrue(np.allclose(res["both"][1], np.maximum(hi_t, hi_b)))

    def test_skew_generator_zero_mean_negative_skew_and_trade_count(self):
        rng = np.random.default_rng(3)
        x, ntr = st.gen_skew(rng, 20000, 21, 2.0, 0.9)
        se = x.std() / math.sqrt(x.size)
        self.assertLess(abs(float(x.mean())), 4 * se)
        self.assertLess(float(np.nanmean(st.sample_skew(x))), -0.3)
        self.assertAlmostEqual(float(ntr.mean()), 2.0 * st.SKEW_NPAIRS * 21, delta=3.0)
        loss = 0.9 * st.SKEW_W / 0.1
        self.assertAlmostEqual(loss / st.SKEW_W, 9.0, places=12)       # отношение проигрыш/выигрыш p/(1−p)

    def test_block_t_rule_oversized_on_rare_big_loss_profile(self):
        # профиль 32:1 (p_win 0,97), 2,5 сделки в сутки клетки: доля «нижняя граница блочного t > 0» при истинном нуле существенно выше 5 %
        xs, _ = st.gen_skew(np.random.default_rng(7), 2000, 21, 0.5, 0.97)
        size_t = float(np.mean([st.bounds_t_block(xs[i])[0] > 0 for i in range(2000)]))
        self.assertGreater(size_t, 0.10)


if __name__ == "__main__":
    unittest.main()
