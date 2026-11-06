"""TK-493 w3 M-04: тест размера интервала по суткам на синтетическом нуле (numpy).

Доля случаев «нижняя граница 90 %-интервала > 0» при среднем 0 должна быть ≈ 5 %: критерий задания - не больше 6 %.
Проверяется боевая функция `portfolio.block_t_interval` (не копия). Перцентильный бутстреп (прежний способ) измерен в size_test.py
и записан в docs/findings/xdl9-stats-fixes-2026-11-06-size.csv (8,0-10,3 % на независимых сутках): здесь только согласованность
его векторной копии с `portfolio.block_bootstrap`, чтобы цифры файла относились к тому, что считает боевой код.
"""
import importlib.util, os, sys, unittest
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


if __name__ == "__main__":
    unittest.main()
