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


if __name__ == "__main__":
    unittest.main()
