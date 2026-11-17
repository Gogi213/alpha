"""Юнит-тесты П-42 §13.17 на синтетике: python3 -m unittest tools/p42/test_sigfeat.py (на srv; numpy, pandas, scipy)."""
import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sigfeat_lib as L  # noqa: E402
import sigfeat_run as RUN  # noqa: E402

PX = 10_000_000_000  # 100.0 в e8


class OutcomeTests(unittest.TestCase):
    def oc(self, prices, x=100_000_000, f=0, R=PX):
        return L.outcome(np.array(prices, dtype=np.int64), R, x, f)

    def test_not_reached(self):
        self.assertEqual(self.oc([PX - 5, PX - 1])[0], L.O1)

    def test_touch_boundary_counts(self):  # сделка ровно на уровне — касание
        self.assertEqual(self.oc([PX - 5, PX])[0], L.O2)

    def test_break_exact_x(self):  # ровно R + x — пробой
        self.assertEqual(self.oc([PX, PX + 100_000_000])[0], L.O3)

    def test_break_before_return(self):
        self.assertEqual(self.oc([PX, PX + 100_000_000, PX - 5])[0], L.O3)

    def test_return_before_break(self):
        self.assertEqual(self.oc([PX, PX - 1, PX + 100_000_000])[0], L.O4)

    def test_return_strict(self):  # цена ровно на уровне после касания — не возврат
        self.assertEqual(self.oc([PX, PX, PX])[0], L.O2)

    def test_return_depth(self):  # возврат глубже f: просто ниже уровня на 1 — при f > 1 не возврат
        self.assertEqual(self.oc([PX, PX - 1], f=10)[0], L.O2)
        self.assertEqual(self.oc([PX, PX - 11], f=10)[0], L.O4)

    def test_short_mirror(self):  # шорт в длинной раме: цены и уровень с обратным знаком
        R = -100 * 100_000_000  # реальный уровень 100 (поддержка), цена подходит сверху
        x = 50_000_000  # X = 0,5
        pr = -np.array([101, 100.2, 100, 99.5], dtype=np.float64) * 1e8
        self.assertEqual(L.outcome(pr.astype(np.int64), R, x, 0)[0], L.O3)
        pr = -np.array([101, 100, 100.1, 99.5], dtype=np.float64) * 1e8
        self.assertEqual(L.outcome(pr.astype(np.int64), R, x, 0)[0], L.O4)  # вернулась выше уровня раньше пробоя
        pr = -np.array([101, 100.2], dtype=np.float64) * 1e8
        self.assertEqual(L.outcome(pr.astype(np.int64), R, x, 0)[0], L.O1)

    def test_ceil_mul(self):
        self.assertEqual(L.ceil_mul(PX, 10000, 1_000_000), 100_000_000)
        self.assertEqual(L.ceil_mul(7, 1, 2), 4)

    def test_touch_window(self):
        t = np.array([0, 10, 20, 30, 40], dtype=np.int64)
        P = np.array([PX - 1, PX, PX + 5, PX + 100_000_000, PX - 1], dtype=np.int64)
        self.assertEqual(L.touch_window_outcome(t, P, 1, 100, PX, 100_000_000), "x")
        self.assertEqual(L.touch_window_outcome(t, P, 1, 15, PX, 100_000_000), "n")
        P2 = np.array([PX - 1, PX, PX - 1, PX + 100_000_000], dtype=np.int64)
        self.assertEqual(L.touch_window_outcome(t[:4], P2, 1, 100, PX, 100_000_000), "r")

    def test_dist(self):
        self.assertEqual(L.dist_e8(PX, PX - 30_000_000), 300_000)


class FeatureTests(unittest.TestCase):
    def setUp(self):
        n = L.N_MIN
        self.m = {k: np.zeros(n, np.int64) for k in ("o", "h", "l", "c", "qv")}
        for t in range(n):
            self.m["o"][t] = self.m["c"][t] = 90 * 100_000_000
            self.m["h"][t] = 90 * 100_000_000 + 1
            self.m["l"][t] = 90 * 100_000_000 - 1
            self.m["qv"][t] = 1000

    def test_count_runs(self):
        self.assertEqual(L.count_runs(np.array([1, 1, 0, 1, 0, 0, 1], bool)), 3)
        self.assertEqual(L.count_runs(np.array([0, 0], bool)), 0)
        self.assertEqual(L.count_runs(np.array([1], bool)), 1)

    def test_pierces_exclude_member_bars(self):
        R = 100 * 100_000_000
        # бары-члены: 10 и 12 (часы); прокол в минутах бара 10 (должен игнорироваться) и два прокола в баре 11 / 13
        for t in range(10 * 60 + 5, 10 * 60 + 8):
            self.m["h"][t] = R + 5
        for t in range(11 * 60 + 5, 11 * 60 + 8):
            self.m["h"][t] = R + 7
        for t in range(13 * 60 + 1, 13 * 60 + 3):
            self.m["h"][t] = R
        t_sig = 14 * 60 + 30
        f = L.level_features(self.m, 1, t_sig, R, [(10, R), (12, R)], 12 * 60 + 60, 10, 100_000, 1_000_000)
        self.assertEqual(f["pierce_ep"], 2)
        self.assertEqual(f["pierce_max_bp"], 7 / R * 1e4)
        self.assertEqual(f["touch_ep"], 2)
        self.assertEqual(f["n_piv"], 2)
        self.assertAlmostEqual(f["spread_bp"], 0.0)

    def test_pierce_gap_between_minutes(self):  # серия прерывается минуткой без данных
        R = 100 * 100_000_000
        for t in (11 * 60 + 5, 11 * 60 + 7):
            self.m["h"][t] = R + 1
        self.m["o"][11 * 60 + 6] = 0
        f = L.level_features(self.m, 1, 14 * 60, R, [(10, R), (12, R)], 13 * 60, 10, 0, 1)
        self.assertEqual(f["pierce_ep"], 2)

    def test_short_pierce(self):  # шорт: прокол вниз (low ≤ уровня), цена обычно выше уровня
        R = -100 * 100_000_000  # рама: −цена
        px = 100 * 100_000_000
        for k in ("o", "h", "l", "c"):
            self.m[k][:] = 110 * 100_000_000
        self.m["h"][:] += 1
        self.m["l"][:] -= 1
        for t in range(11 * 60 + 1, 11 * 60 + 3):
            self.m["l"][t] = px - 3
        f = L.level_features(self.m, -1, 14 * 60, R, [(10, px), (12, px)], 13 * 60, 10, 0, 1)
        self.assertEqual(f["pierce_ep"], 1)
        self.assertEqual(f["pierce_max_bp"], 3 / px * 1e4)

    def test_sparse(self):
        o = self.m["o"]
        t = 2000
        o[t - 10:t] = 0
        f = L.sparse_features(self.m, 0, t)
        self.assertAlmostEqual(f["emp2h"], 10 / 120)
        self.assertAlmostEqual(f["emp6h"], 10 / 360)
        self.assertAlmostEqual(f["flat60"], 0.0)
        self.m["h"][t - 20:t - 15] = self.m["l"][t - 20:t - 15]
        f = L.sparse_features(self.m, 0, t)
        self.assertAlmostEqual(f["flat60"], 5 / 60)

    def test_sparse_listing(self):  # окно короче 30 минут от листинга — пусто
        f = L.sparse_features(self.m, 1990, 2000)
        self.assertTrue(np.isnan(f["emp2h"]))

    def test_tick_sparse(self):
        sig = 10_000_000
        tpre = np.array([sig - 14 * 60_000, sig - 5 * 60_000, sig - 1000], dtype=np.int64)
        f = L.tick_sparse_features(tpre, sig)
        self.assertAlmostEqual(f["tpm15"], 3 / 15)
        self.assertAlmostEqual(f["tpm60"], 3 / 60)
        self.assertAlmostEqual(f["gap60_s"], (60 * 60_000 - 14 * 60_000) / 1000.0)
        self.assertEqual(L.tick_sparse_features(np.empty(0, np.int64), sig)["gap60_s"], 3600.0)

    def test_approach_cons(self):
        R = 100 * 100_000_000
        t = 3000
        for k in range(1, 21):  # 20 закрытий под уровнем в пределах 0,5 %
            self.m["c"][t - k] = R - 10_000_000
        self.m["c"][t - 25] = R - 100_000_000  # 1 % — вне полосы
        self.m["c"][t - 30] = R + 1  # выше уровня — не считается
        cf = L.ffill_close(self.m)
        f = L.approach_features(self.m, cf, 1, t, R, 0.005)
        self.assertEqual(f["cons60"], 20)
        self.assertEqual(f["cons120"], 20)


class StatTests(unittest.TestCase):
    def test_auc_bruteforce(self):
        rng = np.random.default_rng(1)
        x = rng.integers(0, 6, 200).astype(float)
        y = rng.integers(0, 2, 200)
        self.assertAlmostEqual(L.auc(x, y), L.auc_bruteforce(x, y), places=12)

    def test_auc_nan(self):
        x = np.array([1.0, 2.0, np.nan, 3.0])
        y = np.array([0, 1, 1, 1])
        self.assertEqual(L.auc(x, y), 1.0)

    def test_wilson(self):
        lo, hi = L.wilson(5, 10)
        self.assertAlmostEqual(lo, 0.2366, places=3)
        self.assertAlmostEqual(hi, 0.7634, places=3)

    def test_holm(self):
        adj = L.holm([0.01, 0.04, 0.03])
        self.assertAlmostEqual(adj[0], 0.03)
        self.assertAlmostEqual(adj[2], 0.06)
        self.assertAlmostEqual(adj[1], 0.06)

    def test_icc(self):
        y = np.array([1, 1, 1, 0, 0, 0, 1, 0])
        g = np.array([0, 0, 0, 1, 1, 1, 2, 2])
        rho, neff = L.icc_binary(y, g)
        self.assertGreater(rho, 0.5)
        self.assertLess(neff, len(y))

    def test_pearson_nan(self):
        rng = np.random.default_rng(2)
        x, y = rng.normal(size=100), rng.normal(size=100)
        x[::7] = np.nan
        y[::5] = np.nan
        ok = ~(np.isnan(x) | np.isnan(y))
        self.assertAlmostEqual(L.pearson_pairs(x, y, 10), np.corrcoef(x[ok], y[ok])[0, 1], places=12)
        self.assertTrue(np.isnan(L.pearson_pairs(x, y, 1000)))

    def test_corr_matrix_vs_pairs(self):
        rng = np.random.default_rng(3)
        R = rng.normal(size=(6, 300))
        R[rng.random(R.shape) < 0.2] = np.nan
        r, n = L.corr_matrix(R, 20)
        for i in range(6):
            for j in range(6):
                if i != j:
                    self.assertAlmostEqual(r[i, j], L.pearson_pairs(R[i], R[j], 20), places=9)

    def test_block_returns(self):
        m = {k: np.zeros(L.N_MIN, np.int64) for k in ("o", "h", "l", "c", "qv")}
        for t in range(0, 20):
            m["o"][t] = 1
            m["c"][t] = 100 + t
        r = L.block_returns(m, 5)
        self.assertTrue(np.isnan(r[0]))
        self.assertAlmostEqual(r[1], 109 / 104 - 1)
        m["o"][5:10] = 0
        r = L.block_returns(m, 5)
        self.assertTrue(np.isnan(r[1]) and np.isnan(r[2]))

    def test_block_resamples(self):
        days = np.repeat(np.arange(10), 3)
        rs = L.block_resamples(days, 5, 4, np.random.default_rng(0))
        self.assertEqual(len(rs), 5)
        self.assertTrue(all(len(x) <= 36 for x in rs))


class FakeTicks:
    """Сделки: список (мс, цена_e8)."""

    def __init__(self, ticks):
        a = np.array(ticks, dtype=np.int64)
        self.t, self.p = a[:, 0], a[:, 1]

    def span(self, a, b):
        i0, i1 = np.searchsorted(self.t, a, "left"), np.searchsorted(self.t, b, "right")
        return self.t[i0:i1], self.p[i0:i1], True


class EpisodeTests(unittest.TestCase):
    def build(self, ticks, sigs, m):
        w = RUN.Worker.__new__(RUN.Worker)
        tk = FakeTicks(ticks)
        rows, eps = [], []
        w.episode = lambda sym, univ, side, s, row, ep_no, n_sig, last_ms, *a, **k: {"ep_no": ep_no, "n": n_sig, "sig": int(row.sig_ms)}
        df = pd.DataFrame(sigs)
        df["side"] = 1
        w.do_universe("X", "U1", "long", 1, df, m, None, None, None, None, None, None, None, tk, 0, 0.0, rows, eps, {"locate_fail": 0, "sigs": 0})
        return rows, eps

    def mk(self):
        m = {k: np.zeros(L.N_MIN, np.int64) for k in ("o", "h", "l", "c", "qv")}
        return m

    def test_same_episode_then_leave_zone(self):
        R = 100 * 100_000_000
        dist = lambda p: L.dist_e8(R, p)
        px_a, px_b = 9_980_000_000, 9_985_000_000
        t0 = 1000 * 60_000
        ticks = [(t0 + 100, 9_970_000_000), (t0 + 200, px_a),            # сигнал 1 в минуте 1000
                 (t0 + 60_000 + 100, px_b),                               # сигнал 2 в минуте 1001, цена в зоне → тот же эпизод
                 (t0 + 3 * 60_000 + 50, 9_900_000_000),                  # цена ушла дальше E (−1 %) → выход из зоны
                 (t0 + 5 * 60_000 + 100, 9_960_000_000), (t0 + 5 * 60_000 + 200, 9_975_000_000)]  # сигнал 3 в минуте 1005
        sigs = [{"level": 0, "sig_ms": t0 + 200, "dist_e8": dist(px_a), "level_px_e8": R, "members": "1:1", "natr_e8": 1},
                {"level": 0, "sig_ms": t0 + 60_000 + 100, "dist_e8": dist(px_b), "level_px_e8": R, "members": "1:1", "natr_e8": 1},
                {"level": 0, "sig_ms": t0 + 5 * 60_000 + 200, "dist_e8": dist(9_975_000_000), "level_px_e8": R, "members": "1:1", "natr_e8": 1}]
        m = self.mk()
        for t in range(1000, 1006):
            m["o"][t] = 1
            m["h"][t] = 9_990_000_000
            m["l"][t] = 9_990_000_000
        m["l"][1003] = 9_900_000_000
        rows, eps = self.build(ticks, sigs, m)
        self.assertEqual([r["ep_no"] for r in rows], [0, 0, 1])
        self.assertEqual([r["ep_first"] for r in rows], [True, False, True])
        self.assertEqual([e["n"] for e in eps], [2, 1])

    def test_touch_makes_new_episode(self):
        R = 100 * 100_000_000
        px_a = 9_980_000_000
        t0 = 2000 * 60_000
        ticks = [(t0 + 100, px_a), (t0 + 60_000 + 10, 10_000_000_000),   # касание уровня между сигналами
                 (t0 + 60_000 + 20, px_a), (t0 + 3 * 60_000 + 100, px_a)]
        sigs = [{"level": 0, "sig_ms": t0 + 100, "dist_e8": L.dist_e8(R, px_a), "level_px_e8": R, "members": "1:1", "natr_e8": 1},
                {"level": 0, "sig_ms": t0 + 3 * 60_000 + 100, "dist_e8": L.dist_e8(R, px_a), "level_px_e8": R, "members": "1:1", "natr_e8": 1}]
        m = self.mk()
        for t in range(2000, 2004):
            m["o"][t] = 1
            m["h"][t] = 9_990_000_000
            m["l"][t] = 9_970_000_000
        m["h"][2001] = R
        rows, eps = self.build(ticks, sigs, m)
        self.assertEqual([r["ep_no"] for r in rows], [0, 1])


if __name__ == "__main__":
    unittest.main()
