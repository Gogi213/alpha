"""TK-522 (П-40 §16.10): тесты assemble.py — номер суток от --day0 (n_d для p40stats), порог цензуры t_conv, L1-признаки.
Запуск: python tools/p40core/test_assemble.py (нужен numpy)."""
import csv
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ASM = Path(__file__).resolve().parent / "assemble.py"
DAY = 86_400_000
T0 = 1_758_067_200_000  # 2025-09-17 00:00 UTC; значение не важно, важны сутки от --day0


def write(path, head, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(head)
        w.writerows(rows)


def make_core(base, eps, l1):
    """eps: (t0_ms, t_conv_ms, outcome, pnl) — один эпизод = строка во всех пяти таблицах ядра"""
    depth = ["l1_notional", "bookimb1"] if l1 else ["depth_slip", "bookimb"]
    write(base, ["theta", "t0_ms", "t_conv_ms"], [["12.6", t0, tc] for t0, tc, _, _ in eps])
    write(str(base) + ".exits.csv", ["theta", "t0_ms", "pnl_bps", "outcome"], [["12.6", t0, p, o] for t0, _, o, p in eps])
    write(str(base) + ".features.csv", ["theta", "t_start", "hour", "leader", *depth, "spread", "vol5m", "plevel", "flow1m", "d_in"],
          [["12.6", t0, 3, 1, 5.0 + i, 1.0 + i, 2.0, 3.0, 4.0, 5.0, 6.0] for i, (t0, _, _, _) in enumerate(eps)])
    write(str(base) + ".path.csv", ["theta", "t0_ms", "mpu_bps"], [["12.6", t0, -1.0] for t0, _, _, _ in eps])
    write(str(base) + ".aux.csv", ["theta", "t_start", "phi_bps", "liq5m", "oi5m"], [["12.6", t0, 0.5, 0, 0.1] for t0, _, _, _ in eps])


def load(path):
    with np.load(path) as z:
        return {k: z[k] for k in z.files}


def run_asm(d, base, extra):
    out = Path(d) / "t.npz"
    r = subprocess.run([sys.executable, str(ASM), str(out), str(Path(d) / "b.json"), *extra, f"7:t0:1:0:{base}"], capture_output=True, text=True)
    return r, out


class AssembleTests(unittest.TestCase):
    def test_day0_gives_day_numbers_and_cens_threshold(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d) / "core"
            # сутки 0, 3, 13 от day0; t_conv: 30 с (сошлось за T_life), 200 с (сошлось за H=600, не за 60), не сошлось (-1)
            make_core(base, [(T0 + 5_000, 30_000, "both", 4.0), (T0 + 3 * DAY + 5_000, 200_000, "both", 1.0), (T0 + 13 * DAY + 5_000, -1, "none", 0.0)], l1=False)
            r, out = run_asm(d, base, ["--day0", "2025-09-17", "--cens-s", "600"])
            self.assertEqual(r.returncode, 0, r.stderr)
            t = load(out)
            self.assertEqual(t["day"].tolist(), [0, 3, 13], "n_d = max(day)+1 = 14, не ГГГГММДД")
            self.assertEqual(t["cens"].tolist(), [0, 0, 1], "с --cens-s 600 эпизод со сходимостью за 200 с не цензурирован")
            self.assertEqual(t["conv_H"].tolist(), [1, 1, 0])
            self.assertEqual(t["t_conv_s"].tolist(), [30.0, 200.0, 600.0], "цензурированному — порог цензуры")
            r60, out60 = run_asm(d, base, ["--day0", "2025-09-17"])
            self.assertEqual(load(out60)["cens"].tolist(), [0, 1, 1], "умолчание 60 с — прежнее поведение")

    def test_without_day0_day_is_yyyymmdd_as_before(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d) / "core"
            make_core(base, [(T0 + 5_000, 30_000, "both", 4.0)], l1=False)
            r, out = run_asm(d, base, [])
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(load(out)["day"].tolist(), [20250917])

    def test_l1_features_without_keyerror_and_depth_kind(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d) / "core"
            make_core(base, [(T0 + 5_000 + i, 30_000, "both", 1.0) for i in range(6)], l1=True)
            r, out = run_asm(d, base, ["--day0", "2025-09-17"])
            self.assertEqual(r.returncode, 0, r.stderr)
            t = load(out)
            self.assertEqual(set(t["depth_kind"].tolist()), {1})
            self.assertEqual(sorted(set(t["f_depth"].tolist())), [0, 1, 2], "терцили по l1_notional")

    def _funding_case(self, d, ftext, win="2025-09-17:2025-09-17"):
        base = Path(d) / "core"
        t0 = T0 + 1 * 3_600_000                     # 01:00 UTC
        make_core(base, [(t0, 30_000, "both", 4.0), (t0 + 60_000, -1, "none", 0.0)], l1=False)
        # exit_ms: эпизод 1 держится с входа до 09:00 (метка расчёта 08:00 внутри), эпизод 2 — не исполнен
        write(str(base) + ".exits.csv", ["theta", "t0_ms", "pnl_bps", "outcome", "exit_ms"], [["12.6", t0, 4.0, "both", T0 + 9 * 3_600_000], ["12.6", t0 + 60_000, 0.0, "none", -1]])
        # sell_leg B: нога B (шорт) исполнена через 10 с, A (лонг) через 20 с
        write(str(base) + ".fills.csv", ["theta", "t0_ms", "sell_leg", "fill_sell_ms", "fill_buy_ms"], [["12.6", t0, "B", 10_000, 20_000], ["12.6", t0 + 60_000, "B", -1, -1]])
        write(Path(d) / "pairs.csv", ["asset", "pair", "exA", "symA", "exB", "symB"], [[7, 1, "bybit", "XUSDT", "gate", "X_USDT"]])
        write(Path(d) / "f.csv", ["exchange", "symbol", "ts_ms", "rate"], ftext)
        return run_asm(d, base, ["--funding", str(Path(d) / "f.csv"), "--pairs", str(Path(d) / "pairs.csv"), "--funding-window", win])

    def test_funding_in_net_signs_and_column(self):
        D = "2025-09-17:2025-09-17"
        h = lambda x: T0 + x * 3_600_000
        rows = [["bybit", "XUSDT", h(x), 1e-4] for x in (0, 8, 16)] + [["gate", "X_USDT", h(x) + 1000, 3e-4] for x in (0, 8, 16)]
        with tempfile.TemporaryDirectory() as d:
            r, out = self._funding_case(d, rows, D)
            self.assertEqual(r.returncode, 0, r.stderr)
            t = load(out)
            # метка 08:00: A (лонг) платит 1 bps, B (шорт) получает 3 bps -> +2; метки 00:00 до входа и 16:00 после выхода не в счёте
            self.assertAlmostEqual(t["fund_bps"][0], 2.0, places=9)
            self.assertAlmostEqual(t["net_bps"][0], 4.0 + 2.0, places=9)
            self.assertEqual(t["fund_bps"][1], 0.0, "не исполнен — фандинга нет")
            self.assertEqual(t["net_bps"][1], 0.0)

    def test_funding_hole_or_missing_leg_refuses(self):
        h = lambda x: T0 + x * 3_600_000
        with tempfile.TemporaryDirectory() as d:
            r, _ = self._funding_case(d, [["bybit", "XUSDT", h(0), 1e-4], ["bybit", "XUSDT", h(8), 1e-4], ["bybit", "XUSDT", h(16), 1e-4]])
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("нет ставок", r.stderr + r.stdout)
        with tempfile.TemporaryDirectory() as d:
            rows = [["bybit", "XUSDT", h(x), 1e-4] for x in (0, 8, 24)] + [["gate", "X_USDT", h(x), 1e-4] for x in (0, 8, 16)]
            r, _ = self._funding_case(d, rows, "2025-09-17:2025-09-18")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("дыры", r.stderr + r.stdout)

    def test_funding_edges_by_episode_not_window(self):
        h = lambda x: T0 + x * 3_600_000
        gate = [["gate", "X_USDT", h(x), 1e-4] for x in (0, 8, 16)]
        with tempfile.TemporaryDirectory() as d:     # контракт выпущен внутри окна: таблица начинается в 08:00, вход ноги в 01:00 (<= 8 ч до метки) — допустимо
            r, out = self._funding_case(d, [["bybit", "XUSDT", h(x), 1e-4] for x in (8, 16)] + gate)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertAlmostEqual(load(out)["fund_bps"][0], 0.0, places=9, msg="метка 08:00: лонг Bybit платит 1 bps, шорт Gate получает 1 bps -> 0")
        with tempfile.TemporaryDirectory() as d:     # таблица начинается за 15 ч после входа: метки внутри эпизода потеряны -> отказ
            r, _ = self._funding_case(d, [["bybit", "XUSDT", h(x), 1e-4] for x in (16, 24)] + gate)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("не покрывает эпизод", r.stderr + r.stdout)
        with tempfile.TemporaryDirectory() as d:     # таблица кончается за 9 ч до выхода (09:00): последняя метка 00:00 -> отказ
            r, _ = self._funding_case(d, [["bybit", "XUSDT", h(x), 1e-4] for x in (-8, 0)] + gate)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("не покрывает эпизод", r.stderr + r.stdout)

    def test_without_funding_no_column_net_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d) / "core"
            make_core(base, [(T0 + 5_000, 30_000, "both", 4.0)], l1=False)
            r, out = run_asm(d, base, [])
            t = load(out)
            self.assertNotIn("fund_bps", t)
            self.assertEqual(t["net_bps"].tolist(), [4.0])

    def test_stale_flag_from_age_and_smax(self):   # Судья 10.11 §2 п. 2: stale = возраст лучших цен любой ноги на t0 > S_max контракта; нет возраста/S_max -> -1; без --stale-smax столбца нет
        with tempfile.TemporaryDirectory() as d:
            base = Path(d) / "core"
            ts = [T0 + 1_000, T0 + 2_000, T0 + 3_000, T0 + 4_000, T0 + 5_000]
            make_core(base, [(t, 30_000, "both", 1.0) for t in ts], l1=True)
            write(str(base) + ".age.csv", ["t0_ms", "ageA_ms", "ageB_ms"], [[ts[0], 900, 1900], [ts[1], 1100, 100], [ts[2], 100, 2100], [ts[4], -1, 5]])   # ts[3] без строки
            write(Path(d) / "pairs.csv", ["pair", "exA", "symA", "exB", "symB"], [[1, "binance", "XUSDT", "okx", "X-USDT-SWAP"]])
            write(Path(d) / "smax.csv", ["ex", "contract", "s_max_s"], [["binance_futures", "XUSDT", 1.0], ["okx_futures", "X-USDT-SWAP", 2.0]])
            r, out = run_asm(d, base, ["--stale-smax", str(Path(d) / "smax.csv"), "--pairs", str(Path(d) / "pairs.csv")])
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(load(out)["stale"].tolist(), [0, 1, 1, -1, -1])
            self.assertIn("stale 2 возраст неизвестен 2", r.stdout)
            r2, out2 = run_asm(d, base, [])
            self.assertNotIn("stale", load(out2))


if __name__ == "__main__":
    unittest.main()
