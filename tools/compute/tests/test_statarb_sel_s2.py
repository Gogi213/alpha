"""TK-493 S2 (П-37 r3.2): тесты пересчёта отбора по решениям CEO — метки региона США/не-США (D6), HL(лаг 15) (D14), ориентация и вид сетки D2 (D5 i),
активные пары-сутки D2 по панелям (D5 iii), MTM в воротах entry_z (D10), интервалы выплат (D5 i).
Запуск: python -I -m unittest tools/compute/tests/test_statarb_sel_s2.py (из корня дерева; нужен numpy)."""
import csv, gzip, math, os, struct, sys, tempfile, unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
XD = os.path.join(HERE, "..", "statarb", "xdl9")
sys.path.insert(0, XD)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "statarb"))
import asset_class as AC  # noqa: E402
import sel_cells as SC  # noqa: E402
import sel_d2 as SD2  # noqa: E402
import sel_entry as SE  # noqa: E402
import d2_active as DA  # noqa: E402


def row(ex, sym, base, cls="equity", quote="USDT", class_src="", note=""):
    return dict(exchange=ex, symbol=sym, base=base, quote=quote, **{"class": cls}, class_src=class_src, status="Trading", note=note)


class RegionLabels(unittest.TestCase):
    def test_labels_from_primary_fields(self):
        rows = [row("bybit", "AAPLUSDT", "AAPL", note="region=US ticker=AAPL"),
                row("bybit", "BYDUSDT", "BYD", note="region=HK ticker=1211"),
                row("bitget", "LASERTECUSDT", "LASERTEC", note="pt=usdt-futures; листинг JP"),
                row("binance", "SAMSUNGUSDT", "SAMSUNG", class_src="exchange:underlyingType=EQUITY|sub=KR_EQUITY"),
                row("hyperliquid", "XUSD", "XXX", note="region=KR ticker=1"),
                row("bybit", "NOREGION", "NOR", note="region= ticker=1")]
        lab = AC.region_labels(rows)
        self.assertEqual(lab["AAPL"], {"bybit:US"})
        self.assertEqual(lab["BYD"], {"bybit:HK"})
        self.assertEqual(lab["LASERTEC"], {"bitget:JP"})
        self.assertEqual(lab["SAMSUNG"], {"binance:KR"})
        self.assertNotIn("XXX", lab)                      # Hyperliquid не берётся
        self.assertNotIn("NOR", lab)                      # пустой регион - не метка

    def test_verdict_label_beats_data(self):
        self.assertEqual(AC.region_verdict({"bybit:US"})[0], "США")
        self.assertEqual(AC.region_verdict({"bybit:HK", "binance:HK"})[0], "не-США")
        self.assertEqual(AC.region_verdict({"bybit:US", "bitget:KR"})[0], "не-США")     # метка не-США сильнее метки США
        self.assertEqual(AC.region_verdict(set()), (None, ""))                          # нет метки - запасное правило по данным
        self.assertIn("метка биржи", AC.region_verdict({"bybit:US"})[1])


class HalfLifeLag15(unittest.TestCase):
    def _ou(self, phi, n=20160, seed=3, noise=0.0):
        rng = np.random.RandomState(seed)
        e = rng.randn(n)
        s = np.zeros(n)
        for t in range(1, n):
            s[t] = phi * s[t - 1] + e[t]
        sd = float(np.std(s))
        return s + noise * sd * np.random.RandomState(seed + 1).randn(n)

    def test_clean_ou_both_lags_agree(self):
        phi = 0.99
        true_hl = math.log(2) / -math.log(phi)
        s = self._ou(phi)
        _, hl1, _ = SC.hl_of(s, np.zeros_like(s), ols=False)
        _, hl15, _ = SC.hl15_of(s, np.zeros_like(s), ols=False)
        self.assertAlmostEqual(hl1 / true_hl, 1.0, delta=0.3)
        self.assertAlmostEqual(hl15 / true_hl, 1.0, delta=0.35)

    def test_measurement_noise_biases_lag1_down(self):
        # L5-05: шум измерения (отскок цены) занижает HL по лагу 1 в разы, лаг 15 ближе к истине
        phi = 0.995
        true_hl = math.log(2) / -math.log(phi)
        s = self._ou(phi, noise=1.0)
        _, hl1, _ = SC.hl_of(s, np.zeros_like(s), ols=False)
        _, hl15, _ = SC.hl15_of(s, np.zeros_like(s), ols=False)
        self.assertLess(hl1, 0.7 * true_hl)
        self.assertGreater(hl15, hl1)
        self.assertLess(abs(hl15 - true_hl), abs(hl1 - true_hl))

    def test_none_when_too_few(self):
        s = np.full(100, np.nan)
        self.assertIsNone(SC.hl15_of(s, np.zeros(100), ols=False))
        self.assertIsNone(SC.hl15_of(np.arange(10.0), np.zeros(10), ols=True))

    def test_phi_outside_unit_gives_nan(self):
        s = np.arange(5000.0)                                                         # тренд: phi = 1, полураспада нет
        r = SC.hl15_of(s, np.zeros_like(s), ols=False)
        self.assertTrue(r is not None and (r[1] != r[1] or r[1] > 1e5))


class D2Orientation(unittest.TestCase):
    ITV = {("binance", "A"): 4.0, ("bybit", "A"): 8.0, ("okx", "A"): 8.0, ("lighter", "A"): 1.0}

    def test_larger_interval_is_y(self):
        # Y := нога с БОЛЬШИМ интервалом выплат; пара пришла с Y = binance (4 ч), X = bybit (8 ч) - ориентация меняется
        r = SC.orient_d2(self.ITV, "binance", "A", "bybit", "A")
        self.assertEqual(r[:4], ("bybit", "A", "binance", "A"))
        self.assertEqual(r[4:6], (8.0, 4.0))
        self.assertEqual(r[6], "интервал (смена)")
        r = SC.orient_d2(self.ITV, "bybit", "A", "lighter", "A")
        self.assertEqual(r[:4], ("bybit", "A", "lighter", "A"))                          # Y уже реже
        self.assertEqual(r[6], "интервал")

    def test_tie_and_missing_keep_turnover_orientation(self):
        r = SC.orient_d2(self.ITV, "bybit", "A", "okx", "A")                             # 8 ч и 8 ч - ничья
        self.assertEqual(r[:4], ("bybit", "A", "okx", "A"))
        self.assertEqual(r[6], "оборот")
        r = SC.orient_d2(self.ITV, "bybit", "A", "kraken", "NOFILE")                     # нет данных
        self.assertEqual(r[:4], ("bybit", "A", "kraken", "NOFILE"))
        self.assertEqual(r[6], "оборот")

    def test_grid_kind(self):
        self.assertEqual(SD2.grid_kind("8", "4", 0), "Y реже")
        self.assertEqual(SD2.grid_kind("8", "4", 1), "Y реже")
        self.assertEqual(SD2.grid_kind("8", "8", 1), "равные")
        self.assertEqual(SD2.grid_kind("8", "8", "0"), "сдвиг")
        self.assertEqual(SD2.grid_kind("", "", "1"), "равные")


class ShiftedPanelWeekends(unittest.TestCase):
    """D13: у полос с торговыми днями выходные цели в сдвинутых панелях остаются NaN (иначе у выравниваний без реальной серии ≈ 11 сессий вместо 7 и повторяющиеся блоки)."""

    def test_trade_mode_blanks_weekends_only(self):
        import sel_deck_prep as DP
        import sel_shift as SH
        days = range(2467, 2478)                        # окно EG 03.10-13.10
        blank = [d for d in days if DP.target_blank("trade", d)]
        self.assertEqual(blank, [2467, 2468, 2474, 2475])            # 03.10 сб, 04.10 вс, 10.10 сб, 11.10 вс
        self.assertTrue(all(not SH.is_weekday(d) for d in blank))
        self.assertEqual(sum(1 for d in days if not DP.target_blank("trade", d)), 7)      # будних суток 7: как у реальных пар классов с сессией

    def test_cal_mode_keeps_all_days(self):
        import sel_deck_prep as DP
        self.assertFalse(any(DP.target_blank("cal", d) for d in range(2467, 2478)))


def write_sap(path, first_min, op):
    n = len(op)
    with open(path, "wb") as f:
        f.write(b"SAP1" + struct.pack("<I", 0) + struct.pack("<qQ", first_min, n))
        f.write(np.asarray(op, dtype="<f8").tobytes())
        f.write(np.asarray(op, dtype="<f8").tobytes())


class D2ActiveDays(unittest.TestCase):
    """D5 iii: сутки активны, если open обеих ног конечен хотя бы в одну минуту суток И к концу суток накоплено >= k меток ряда d."""

    def test_two_conditions(self):
        d0 = 2465
        with tempfile.TemporaryDirectory() as tmp:
            n = 3 * 1440
            y = np.full(n, 100.0)
            x = np.full(n, 50.0)
            x[1440:2 * 1440] = np.nan                               # у X вторые сутки без open
            py, px = os.path.join(tmp, "Y.sap"), os.path.join(tmp, "X.sap")
            write_sap(py, d0 * 1440, y)
            write_sap(px, d0 * 1440, x)
            series = os.path.join(tmp, "Y.csv.gz")
            # метки ряда d: две в первые сутки (после 0:00), третья - во вторые сутки, дальше - в третьи
            marks_min = [d0 * 1440 + 100, d0 * 1440 + 900, (d0 + 1) * 1440 + 100, (d0 + 2) * 1440 + 100]
            with gzip.open(series, "wt", encoding="utf-8", newline="") as fh:
                fh.write("time_ms,rate,interval_h\n")
                for m in marks_min:
                    fh.write("%d,0.0001,8\n" % (DA.GRID0_MS + m * 60_000))
            act = DA.pair_active(py, px, series, k=3, day0=d0, day1=d0 + 3)
            self.assertEqual(act, [0, 0, 1])                         # сутки 1: меток 2 < k; сутки 2: X без open; сутки 3: обе зелёные
            act1 = DA.pair_active(py, px, series, k=1, day0=d0, day1=d0 + 3)
            self.assertEqual(act1, [1, 0, 1])


class EntryMtm(unittest.TestCase):
    def test_add_mtm_into_last_day(self):
        agg = {"c|z1.00": {2470: [4.0, 100.0]}}
        fee = {"c|z1.00": [50.4, 4.0]}
        mtm = {"c|z1.00": [2.0, -30.0, 25.2, -55.2], "c|z1.25": [0.0, 0.0, 0.0, 0.0], "c|z2.00": [1.0, 5.0, 12.6, -7.6]}
        SE.add_mtm(agg, fee, mtm)
        self.assertEqual(agg["c|z1.00"][2470], [4.0, 100.0])
        self.assertEqual(agg["c|z1.00"][SE.LAST_DAY], [2.0, -30.0])                  # MTM - в последние сутки формирования
        self.assertEqual(fee["c|z1.00"], [75.6, 6.0])
        self.assertNotIn("c|z1.25", agg)                                              # нулевая MTM-выборка ничего не создаёт
        self.assertEqual(agg["c|z2.00"][SE.LAST_DAY], [1.0, 5.0])
        N, mean, lo, hi = SE.day_cluster_ci(agg["c|z1.00"])
        self.assertEqual(N, 6)
        self.assertAlmostEqual(mean, 70.0 / 6.0)

    def test_mtm_censoring_changes_gate(self):
        # правое цензурирование (L6-03): закрытые сделки +30 bps валового, две открытые позиции -60 bps каждая - ворота «валовый >= 2 x комиссия» падают
        agg = {"k": {2470: [20.0, 600.0]}}
        fee = {"k": [20 * 12.6, 20.0]}
        N0, mg0, _, _ = SE.day_cluster_ci(agg["k"])
        self.assertGreaterEqual(mg0, 2 * 12.6)
        SE.add_mtm(agg, fee, {"k": [2.0, -120.0, 25.2, -145.2]})
        N1, mg1, _, _ = SE.day_cluster_ci(agg["k"])
        self.assertEqual(N1, 22)
        self.assertLess(mg1, mg0)
        self.assertAlmostEqual(mg1, 480.0 / 22.0)


if __name__ == "__main__":
    unittest.main()
