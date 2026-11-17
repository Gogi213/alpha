"""Юнит-тесты П-42 §13.20 (TK-594, ворота G4): окна потока [a, b), знак по стороне, ускорение, корзины vpin, q99 по гистограммам, T* и дыры, лаг, фандинг, премия.
Запуск на srv: cd /data/tk594/py && python3 -m unittest test_tk594 (numpy, pandas)."""
import math
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk594_feat as F  # noqa: E402

DAY, MIN, HOUR = F.DAY_MS, F.MIN_MS, F.HOUR_MS
SIG = 1_790_000_000_000 // DAY * DAY + 12 * HOUR  # полдень UTC


def arrs(recs):
    recs = sorted(recs)
    return (np.array([r[0] for r in recs], np.int64), np.array([r[1] for r in recs], float), np.array([r[2] for r in recs], bool))


class FlowTests(unittest.TestCase):
    def test_window_boundaries_and_trigger_excluded(self):
        # запись ровно на sig − 10 с входит; на sig (сигнальная) и позже — нет; на sig − 10 с − 1 мс — нет
        recs = [(SIG - 10_000 - 1, 100.0, True), (SIG - 10_000, 10.0, True), (SIG - 1, 30.0, False), (SIG, 1000.0, True), (SIG + 5, 1000.0, True)]
        T, V, B = arrs(recs)
        f = F.flow_features(T, V, B, SIG, 1, None, False)
        self.assertAlmostEqual(f["tfv10"], (10.0 - 30.0) / 40.0)
        self.assertAlmostEqual(f["tfn10"], 0.0)
        self.assertTrue(math.isnan(f["tfv60"]) is False)  # 60-секундное окно содержит те же две записи (и запись sig−10с−1 мс)
        self.assertAlmostEqual(f["tfv60"], (110.0 - 30.0) / 140.0)

    def test_sign_by_side(self):
        recs = [(SIG - 5000, 10.0, True), (SIG - 4000, 30.0, False)]
        T, V, B = arrs(recs)
        a = F.flow_features(T, V, B, SIG, 1, None, False)
        b = F.flow_features(T, V, B, SIG, -1, None, False)
        for k in ("tfv10", "tfv300", "tfn10", "tfn1800"):
            self.assertAlmostEqual(a[k], -b[k])
        self.assertAlmostEqual(a["tfv10"], -0.5)  # шорт: продажи тейкера к уровню — «+»

    def test_empty_window_is_nan(self):
        T, V, B = arrs([(SIG - 3_000_000, 5.0, True)])
        f = F.flow_features(T, V, B, SIG, 1, None, False)
        self.assertTrue(math.isnan(f["tfv10"]) and math.isnan(f["tfn300"]) and math.isnan(f["lmsz300"]))

    def test_acceleration_constant_rate_is_one(self):
        recs = [(SIG - k * 1000, 2.0, bool(k % 2)) for k in range(1, 1861)]  # одна запись в секунду на 31 мин
        T, V, B = arrs(recs)
        f = F.flow_features(T, V, B, SIG, 1, None, False)
        self.assertAlmostEqual(f["acc_v"], 1.0, places=2)
        self.assertAlmostEqual(f["acc_n"], 1.0, places=2)

    def test_mean_size_and_ratio(self):
        recs = [(SIG - 100_000, 10.0, True), (SIG - 200_000, 30.0, False), (SIG - 20 * MIN, 100.0, True), (SIG - 21 * MIN, 300.0, True)]
        T, V, B = arrs(recs)
        f = F.flow_features(T, V, B, SIG, 1, None, False)
        self.assertAlmostEqual(f["lmsz300"], math.log10(20.0))
        self.assertAlmostEqual(f["msz_r"], 20.0 / 200.0)

    def test_big_share_uses_q99_and_threshold_inclusive(self):
        recs = [(SIG - 1000, 100.0, True), (SIG - 2000, 50.0, True), (SIG - 3000, 50.0, False)]
        T, V, B = arrs(recs)
        f = F.flow_features(T, V, B, SIG, 1, 100.0, False)
        self.assertAlmostEqual(f["big300"], 0.5)  # v ≥ q99 включительно
        f2 = F.flow_features(T, V, B, SIG, 1, float("inf"), False)
        self.assertEqual(f2["big300"], 0.0)
        f3 = F.flow_features(T, V, B, SIG, 1, None, False)
        self.assertTrue(math.isnan(f3["big300"]))

    def test_tfp300_normalised_by_day_turnover(self):
        recs = [(SIG - 2 * DAY // 3 + k * 1000, 1.0, True) for k in range(0, 100)]  # 100 записей за ~16 ч → V24 = 100... плюс недавние
        recs += [(SIG - 60_000, 14.0, True), (SIG - 50_000, 6.0, False)]
        T, V, B = arrs(recs)
        f = F.flow_features(T, V, B, SIG, 1, None, True)
        v24 = 100.0 + 20.0
        self.assertAlmostEqual(f["tfp300"], (14.0 - 6.0) / (v24 / 288.0))
        g = F.flow_features(T, V, B, SIG, 1, None, False)
        self.assertTrue(math.isnan(g["tfp300"]) and math.isnan(g["vpin"]))  # нет 24 ч записей монеты

    def test_vpin_all_buys_is_one_and_balanced_is_zero(self):
        n = 1000
        recs = [(SIG - DAY + 1000 + k * 80_000, 1.0, True) for k in range(n)]
        T, V, B = arrs(recs)
        self.assertAlmostEqual(F.vpin_value(V, B, 0, n), 1.0)
        recs2 = [(SIG - DAY + 1000 + k * 80_000, 1.0, bool(k % 2)) for k in range(n)]
        T, V, B = arrs(recs2)
        self.assertAlmostEqual(F.vpin_value(V, B, 0, n), 0.0)

    def test_vpin_bucket_edges_close_the_bucket_and_empty_are_skipped(self):
        # 100 записей по 1: цель k/50·100 = 2k → граница — запись с накопленным 2k (2-я, 4-я, …), каждая корзина — 2 записи
        V = np.ones(100)
        B = np.array([True, False] * 50)
        self.assertAlmostEqual(F.vpin_value(V, B, 0, 100), 0.0)
        # корзины из 2 записей, обе покупки → 1; смешанные пары (покупка, продажа) → 0
        B2 = np.array([True, True, True, False] * 25)
        self.assertAlmostEqual(F.vpin_value(V, B2, 0, 100), 0.5)
        # пять крупных записей → < 10 непустых корзин → пусто
        self.assertTrue(math.isnan(F.vpin_value(np.array([10.0, 10.0, 10.0, 10.0, 10.0]), np.array([True] * 5), 0, 5)))

    def test_q99_edge_against_numpy_quantile(self):
        rng = np.random.default_rng(1)
        v = np.exp(rng.normal(3.0, 1.5, 200_000))
        ix = np.clip(np.searchsorted(F.EDGES_ARR, v, "right") - 1, 0, F.NB - 1)
        hist = np.bincount(ix, minlength=F.NB)
        q = F.q99_edge(hist)
        ref = float(np.quantile(v, 0.99))
        self.assertLessEqual(abs(math.log10(q) - math.log10(ref)), 0.021)
        self.assertLessEqual(float((v >= q).mean()), 0.0101)
        self.assertIsNone(F.q99_edge(np.zeros(F.NB, np.int64)))
        top = np.zeros(F.NB, np.int64)
        top[F.NB - 1] = 100  # вся масса в верхней корзине: доля ≥ границы никогда не ≤ 1 % → inf
        self.assertTrue(math.isinf(F.q99_edge(top)))


class DerivTests(unittest.TestCase):
    D0 = SIG // DAY - 2

    def grid(self, fill=None):
        n = 3 * 288 + 288
        cols = ["sum_open_interest", "sum_open_interest_value", "count_toptrader_long_short_ratio", "sum_toptrader_long_short_ratio", "count_long_short_ratio"]
        M = {c: np.full(n, 1.0) for c in cols}
        for i in range(n):
            M["sum_open_interest"][i] = 100.0 + i
        return M

    def test_tstar_and_lag(self):
        sig = SIG + 7 * MIN + 123  # 12:07:00.123 → sig − 10 мин = 11:57:00.123 → T* = 11:55
        Ts = (sig - 10 * MIN) // 300_000 * 300_000
        self.assertEqual(Ts, SIG - 5 * MIN)
        M = self.grid()
        g = (Ts - self.D0 * DAY) // 300_000
        first = self.D0 * DAY
        f = F.deriv_features(sig, 1000.0, M, first, self.D0, np.array([], np.int64), np.array([]), np.full(10, np.nan))
        oi, oi12 = 100.0 + g, 100.0 + g - 12
        self.assertAlmostEqual(f["oi_c1"], (oi / oi12 - 1) * 1e4)
        self.assertAlmostEqual(f["loi_turn"], math.log10(1.0 / 1000.0))
        # мусор в строках позже T* не меняет ответ
        M2 = {c: v.copy() for c, v in M.items()}
        for c in M2:
            M2[c][g + 1:] = 9e9
        f2 = F.deriv_features(sig, 1000.0, M2, first, self.D0, np.array([], np.int64), np.array([]), np.full(10, np.nan))
        self.assertAlmostEqual(f["oi_c24"], f2["oi_c24"])

    def test_hole_gives_nan_not_earlier_row(self):
        sig = SIG + 7 * MIN
        M = self.grid()
        Ts = (sig - 10 * MIN) // 300_000 * 300_000
        g = (Ts - self.D0 * DAY) // 300_000
        for c in M:
            M[c][g] = np.nan
        f = F.deriv_features(sig, 1000.0, M, self.D0 * DAY, self.D0, np.array([], np.int64), np.array([]), np.full(10, np.nan))
        self.assertTrue(all(math.isnan(f[k]) for k in ("oi_c1", "oi_c4", "oi_c24", "loi_turn", "ls_tc", "ls_a")))

    def test_new_listing_all_metrics_nan(self):
        sig = SIG + 7 * MIN
        Ts = (sig - 10 * MIN) // 300_000 * 300_000
        f = F.deriv_features(sig, 1000.0, self.grid(), Ts - 3 * HOUR, self.D0, np.array([], np.int64), np.array([]), np.full(10, np.nan))
        self.assertTrue(all(math.isnan(f[k]) for k in F.DERIV[:10]))

    def test_log_ratios_and_changes(self):
        sig = SIG + 7 * MIN
        M = self.grid()
        Ts = (sig - 10 * MIN) // 300_000 * 300_000
        g = (Ts - self.D0 * DAY) // 300_000
        M["count_toptrader_long_short_ratio"][g] = 2.0
        M["count_toptrader_long_short_ratio"][g - 12] = 1.0
        M["sum_toptrader_long_short_ratio"][g] = 0.0  # ≤ 0 → пусто
        f = F.deriv_features(sig, 1000.0, M, self.D0 * DAY, self.D0, np.array([], np.int64), np.array([]), np.full(10, np.nan))
        self.assertAlmostEqual(f["ls_tc"], math.log(2.0))
        self.assertAlmostEqual(f["dls_tc"], math.log(2.0))
        self.assertTrue(math.isnan(f["ls_ts"]) and math.isnan(f["dls_ts"]))

    def test_funding_last_change_interval(self):
        sig = SIG + 3 * MIN
        # расчёты каждые 4 ч: последний ≤ sig — 12:00 (SIG), предыдущий 08:00; сутки назад: 12:00 вчера
        ft = np.array([SIG - DAY - 4 * HOUR, SIG - DAY, SIG - 4 * HOUR, SIG, SIG + 4 * HOUR], np.int64)
        fr = np.array([0.0001, 0.0002, 0.0003, 0.0010, 0.5])
        f = F.deriv_features(sig, 1.0, self.grid(), None, self.D0, ft, fr, np.full(10, np.nan))
        self.assertAlmostEqual(f["fund_last"], 10.0)  # бп = ставка·1e4
        self.assertAlmostEqual(f["fund_chg24"], (0.0010 - 0.0002) * 1e4)
        self.assertEqual(f["fund_int"], 4.0)
        ft1 = np.array([SIG - HOUR, SIG], np.int64)
        f1 = F.deriv_features(sig, 1.0, self.grid(), None, self.D0, ft1, np.array([0.0, 0.0001]), np.full(10, np.nan))
        self.assertEqual(f1["fund_int"], 1.0)
        self.assertTrue(math.isnan(f1["fund_chg24"]))  # раньше суток назад расчётов нет

    def test_funding_settlement_exactly_at_sig_counts(self):
        # funding_time ≤ sig — включительно (расчёт опубликован в момент расчёта)
        ft = np.array([SIG - 8 * HOUR, SIG], np.int64)
        f = F.deriv_features(SIG, 1.0, self.grid(), None, self.D0, ft, np.array([0.0001, 0.0005]), np.full(10, np.nan))
        self.assertAlmostEqual(f["fund_last"], 5.0)

    def test_basis_is_last_closed_minute(self):
        sig = SIG + 3 * MIN + 500
        prem = np.full(4 * 1440, np.nan)
        k = (sig // MIN - 1) - (self.D0 * DAY // MIN)
        prem[k] = -0.0005
        prem[k + 1] = 0.9  # текущая (ещё не закрытая) минута не берётся
        f = F.deriv_features(sig, 1.0, self.grid(), None, self.D0, np.array([], np.int64), np.array([]), prem)
        self.assertAlmostEqual(f["basis"], -5.0)
        prem[k] = np.nan
        f = F.deriv_features(sig, 1.0, self.grid(), None, self.D0, np.array([], np.int64), np.array([]), prem)
        self.assertTrue(math.isnan(f["basis"]))


if __name__ == "__main__":
    unittest.main()
