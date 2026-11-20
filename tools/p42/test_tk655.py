#!/usr/bin/env python3
"""Тесты TK-655 (§13.55, §13.55.1) на синтетике: клетки, соседи, правило п. 7 (а)–(ж) с охраной суммы K, G-k0, ряд K, сутки ленты. Без движка и без P&L."""
from __future__ import annotations

import datetime
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk604_stats as st  # noqa: E402
import tk655_run as t  # noqa: E402


def row(d, p=0.5, n=300, months=(60, 60, 60, 60, 30), dm=(0.1, 0.1, 0.1, 0.1, 0.1), rd=0.1, rc=0.1, sh=1.0, sc=100.0, dsum=10.0, se_sum=5.0):
    return dict(n=n, months=[(m, 0.0) for m in months], delta=dict(d=d, p=p, dsum=dsum, se_sum=se_sum, s_c=sc), dmonths=list(dm), robust_days=rd, robust_coins=rc, sharpe=sh)


def rows_all(side, f=lambda c: 0.0, **kw):
    return {c["cid"]: row(f(c), **kw) for c in t.cells(side)}


class Cells(unittest.TestCase):
    def test_counts(self):
        L, S = t.cells("L"), t.cells("S")
        self.assertEqual([c["cid"] for c in L], ["E_e0.05", "E_e0.1", "E_e0.15", "E_e0.2", "E_e0.5", "D_e2_0.025", "D_e2_0.05", "D_e2_0.1",
                                                 "K_k5", "K_k10", "K_k15", "K_k20", "K_k25"])
        self.assertEqual([c["cid"] for c in S], ["E_e0.05", "E_e0.1", "E_e0.2", "E_e0.3", "E_e0.5", "D_e2_0", "D_e2_0.025", "D_e2_0.1"])
        self.assertEqual((len(L), len(S)), (13, 8))
        self.assertEqual((t.trials("L"), t.trials("S")), (33, 28))

    def test_patch(self):
        by = {c["cid"]: c for c in t.cells("S")}
        self.assertEqual(by["E_e0.3"]["patch"], {"e": 0.3})
        self.assertEqual(by["D_e2_0"]["patch"], {"e2": 0})
        self.assertEqual({c["cid"]: c for c in t.cells("L")}["K_k10"]["patch"], {})
        self.assertEqual(t.t.patch_str({"e2": 0}), "e2=0")


class Neighbours(unittest.TestCase):
    def nb(self, side, cid):
        cs = t.cells(side)
        return t.neighbours(side, {c["cid"]: c for c in cs}[cid], t.ax_index(cs))

    def test_skip_base(self):
        self.assertEqual(self.nb("L", "E_e0.2"), {"e": ["E_e0.15", "E_e0.5"]})       # 0,3 — база лонга, берётся 0,5
        self.assertEqual(self.nb("L", "E_e0.5"), {"e": ["E_e0.2"]})
        self.assertEqual(self.nb("S", "E_e0.1"), {"e": ["E_e0.05", "E_e0.2"]})       # 0,15 — база шорта
        self.assertEqual(self.nb("S", "D_e2_0.025"), {"e2": ["D_e2_0", "D_e2_0.1"]})  # 0,05 — база шорта
        self.assertEqual(self.nb("L", "D_e2_0.025"), {"e2": ["D_e2_0.05"]})         # 0 — база лонга, левее нет
        self.assertEqual(self.nb("L", "K_k5"), {"k": ["K_k10"]})
        self.assertEqual(self.nb("L", "K_k25"), {"k": ["K_k20"]})


class Verdict(unittest.TestCase):
    def test_none(self):
        v = t.verdict_side("L", rows_all("L"))
        self.assertEqual(v["passed"], [])
        self.assertEqual(v["answer"], "нет: база стороны остаётся")
        self.assertEqual((v["m"], v["trials_noise"]), (13, 33))

    def test_pass_e_plateau(self):
        good = {"E_e0.1", "E_e0.15", "E_e0.05", "E_e0.2"}
        r = rows_all("L", f=lambda c: 1.0 if c["cid"] in good else 0.0, p=0.9, sh=1.0)
        for c in good:
            r[c] = row(1.0, p=1e-6, sh=8.0)
        v = t.verdict_side("L", r)
        self.assertIn("E_e0.1", v["passed"])
        self.assertIn("кандидат вперёд", v["answer"])
        self.assertNotIn("E_e0.2", v["passed"])          # сосед 0,5 с Δ 0 — плато не держится

    def test_k_sum_strict(self):
        r = rows_all("L", p=0.9)
        for c in ("K_k5", "K_k10", "K_k15"):
            r[c] = row(1.0, p=1e-6, sh=8.0, dsum=-1.0, se_sum=5.0)      # ΔΣ ≥ −SE, но < 0
        v = t.verdict_side("L", r)
        self.assertFalse(v["cells"]["K_k10"]["gates"]["e"])
        self.assertEqual(v["passed"], [])
        for c in ("K_k5", "K_k10", "K_k15"):
            r[c] = row(1.0, p=1e-6, sh=8.0, dsum=1.0)
        v = t.verdict_side("L", r)
        self.assertIn("K_k10", v["passed"])
        self.assertIn("кандидат для ключа движка", v["answer"])

    def test_e_sum_loose(self):
        r = rows_all("S", p=0.9)
        for c in ("E_e0.05", "E_e0.1", "E_e0.2"):
            r[c] = row(1.0, p=1e-6, sh=8.0, dsum=-1.0, se_sum=5.0)
        v = t.verdict_side("S", r)
        self.assertTrue(v["cells"]["E_e0.1"]["gates"]["e"])
        self.assertIn("E_e0.1", v["passed"])

    def test_months_and_min(self):
        r = rows_all("S", p=0.9)
        for c in ("E_e0.05", "E_e0.1", "E_e0.2"):
            r[c] = row(1.0, p=1e-6, sh=8.0, dm=(0.1, -0.1, -0.1, 0.1, 0.1))
        self.assertEqual(t.verdict_side("S", r)["passed"], [])
        for c in ("E_e0.05", "E_e0.1", "E_e0.2"):
            r[c] = row(1.0, p=1e-6, sh=8.0, months=(60, 60, 19, 60, 30))
        self.assertEqual(t.verdict_side("S", r)["passed"], [])


class K0(unittest.TestCase):
    def test_gate(self):
        e = np.arange(200, dtype=float)
        ok, nb, sd, bad = t.k0_gate(e, e + 0.001)
        self.assertTrue(ok)
        self.assertEqual(nb, 0)
        r = e.copy()
        r[0] = np.nan                          # у повтора нет сделки (P&L базы 0)
        r[7] += 0.5
        ok, nb, sd, bad = t.k0_gate(e, r)
        self.assertTrue(ok)                    # 198 из 200 = 99 %, |ΣΔ| = 0,5 ≤ 1
        self.assertEqual((nb, bad), (2, [0, 7]))
        self.assertAlmostEqual(sd, 0.5)
        r = e.copy()
        r[3] = np.nan
        ok, nb, sd, bad = t.k0_gate(e, r)
        self.assertFalse(ok)                   # |ΣΔ| = 3 > 1
        self.assertAlmostEqual(sd, -3.0)
        r = e.copy()
        r[:3] += 1.0
        ok, nb, sd, bad = t.k0_gate(e, r)
        self.assertFalse(ok)                   # 3 из 200 — больше 1 %


class KSeries(unittest.TestCase):
    def test_subset(self):
        b = st.make_series([0, 1, 2, 3], [0, 1, 0, 1], [1.0, 2.0, 3.0, 4.0], [10, 11, 12, 13], ["A", "B", "A", "B"], [20, 21, 22, 23], [15, 16, 17, 18], [0, 0, 0, 0])
        ser, idx = t.k_series(st, b, [0, -3, 2, -2], [5.0, np.nan, 6.0, np.nan])
        self.assertEqual(list(idx), [0, 2])
        self.assertEqual(list(ser.day), [0, 2])
        self.assertEqual(list(ser.val), [5.0, 6.0])
        self.assertEqual(ser.sym_name, ["A", "A"])
        self.assertEqual(list(ser.key_ms), [10, 12])


class Tape(unittest.TestCase):
    def test_cap(self):
        ms = lambda y, m, d, h=0: int(datetime.datetime(y, m, d, h, tzinfo=datetime.timezone.utc).timestamp() * 1000)   # noqa: E731
        days = t.tape_days(ms(2026, 11, 12, 23), ms(2026, 11, 13, 5), datetime.date(2026, 11, 13), st.LAST_MS)
        self.assertEqual(days, [datetime.date(2026, 11, 12), datetime.date(2026, 11, 13)])
        days = t.tape_days(ms(2026, 8, 24, 0) + 30_000, ms(2026, 8, 24, 3), datetime.date(2026, 8, 25), st.LAST_MS)
        self.assertEqual(days, [datetime.date(2026, 8, 23), datetime.date(2026, 8, 24), datetime.date(2026, 8, 25)])


if __name__ == "__main__":
    unittest.main()
