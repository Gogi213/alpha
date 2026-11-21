#!/usr/bin/env python3
"""Тесты TK-669 (§13.69) на синтетике: клетки, OPPD, ворота (а), правило п. 7 (семьи E и X), вид выхода `takes`. Без движка и без P&L."""
from __future__ import annotations

import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk669_run as t  # noqa: E402

H = 3_600_000
E8 = 100_000_000


def row(d=0.5, p=0.0001, n=300, months=(60, 60, 60, 60, 30), dm=(0.1, 0.1, 0.1, 0.1, 0.1), rd=0.1, rc=0.1, sh=9.0, sc=100.0, dsum=10.0, se_sum=5.0, **kw):
    r = dict(n=n, months=[(m, 0.0) for m in months], delta=dict(d=d, p=p, dsum=dsum, se_sum=se_sum, s_c=sc), dmonths=list(dm),
             robust_days=rd, robust_coins=rc, sharpe=sh, dropped=False, inert=False, by_selection=False)
    r.update(kw)
    return r


def rows_all(**kw):
    return {c["cid"]: row(**kw) for c in t.cells()}


class Cells(unittest.TestCase):
    def test_cells(self):
        cs = t.cells()
        self.assertEqual(len([c for c in cs if c["fam"] == "E"]), 10)
        self.assertEqual([c["cid"] for c in cs if c["fam"] == "X"], ["X61_S", "X61_L"])
        cb = t.by_cid()
        self.assertEqual(cb["E58_S"]["patch"], {"xb": 0.4, "e2": 0})
        self.assertEqual(cb["E58_L"]["patch"], {"xb": 0.4})
        self.assertEqual(cb["X61_S"]["patch"]["tt"], 0)
        self.assertEqual(cb["X61_S"]["patch"]["e2"], 0)
        self.assertNotIn("tt", cb["X61_L"]["patch"])
        self.assertNotIn("e2", cb["X61_L"]["patch"])
        self.assertEqual(t.patch_line(cb["E59_S"]["patch"]), "fa=OPPD fo=lo fv=1")
        self.assertEqual(t.patch_line(cb["X61_L"]["patch"]),
                         "n_tp=2 t_low=4 t_high=12 tpp=1 q=100 s=4 bm=2 trp=1 rr=2 rf=0")
        self.assertEqual(len(set(c["cid"] for c in cs)), 12)

    def test_families(self):
        v = t.verdict(rows_all())
        self.assertEqual((v["m_E"], v["m_X"], v["trials_E"], v["trials_X"]), (71, 2, 71, 401))


class Oppd(unittest.TestCase):
    def test_min_over_24h_window(self):
        # сигнал в t = 30 ч; входы противоположной стороны на 2 ч (вне окна), 10 ч и 29 ч; L = 100, NATR = 1 % → 1 NATR = 1,0
        v = t.oppd(["A"], [30 * H], [100 * E8], [int(0.01 * E8)], ["A", "A", "A"], [2 * H, 10 * H, 29 * H], [90 * E8, 98 * E8, 101.5 * E8])
        self.assertAlmostEqual(v[0], 1.5, places=6)               # вход на 2 ч не учтён (старше 24 ч); из 10 ч и 29 ч — min(2, 1,5)

    def test_boundaries(self):
        n = int(0.01 * E8)
        v = t.oppd(["A"], [24 * H], [100 * E8], [n], ["A"], [0], [99 * E8])
        self.assertAlmostEqual(v[0], 1.0, places=6)               # ровно t − 24 ч входит
        v = t.oppd(["A"], [24 * H], [100 * E8], [n], ["A"], [24 * H], [99 * E8])
        self.assertTrue(np.isnan(v[0]))                           # вход в момент t не считается ([t − 24 ч, t))

    def test_no_value(self):
        v = t.oppd(["A", "B", "A"], [10 * H] * 3, [100 * E8] * 3, [int(0.01 * E8), int(0.01 * E8), -1], ["A"], [H], [99 * E8])
        self.assertAlmostEqual(v[0], 1.0, places=6)
        self.assertTrue(np.isnan(v[1]))                           # другая монета — признак пуст
        self.assertTrue(np.isnan(v[2]))                           # нет NATR — признак пуст
        self.assertFalse(bool(v[1] < t.OPPD_MAX))                 # NaN сигнал не отбрасывает


class Gates(unittest.TestCase):
    def test_gate_a(self):
        self.assertTrue(t.gate_a(100, [25, 25, 25, 25, 10]))
        self.assertFalse(t.gate_a(100, [25, 25, 25, 25, 7]))
        self.assertFalse(t.gate_a(60, [25, 25, 25, 25, 10]))

    def test_exit_kind_takes_accepted(self):
        import tk620_rr as rr
        s = rr.rr_stats([dict(pnl=10.0, kind="takes"), dict(pnl=-5.0, kind="stop"), dict(pnl=3.0, kind="trail")])
        self.assertEqual(s["kinds"]["takes"]["n"], 1)
        self.assertEqual(s["stops"]["n"], 1)


class Rule(unittest.TestCase):
    def test_all_pass(self):
        v = t.verdict(rows_all(p=1e-9))
        self.assertEqual(len(v["passed"]), 12)
        self.assertTrue(v["answer"].startswith("кандидат вперёд"))
        self.assertIsNone(v["cells"]["E57_S"]["gates"]["f"])      # плато — только строка 60
        self.assertIsNotNone(v["cells"]["E60_S05"]["gates"]["f"])

    def test_holm_prior_61(self):
        # p = 0,001 > 0,05/71 (7,0e-4) — клетки E не проходят Холм; клетки X (m = 2, порог 0,025) проходят
        v = t.verdict(rows_all(p=0.001))
        self.assertFalse(any(c["gates"]["b"] for k, c in v["cells"].items() if k[0] == "E"))
        self.assertTrue(all(c["gates"]["b"] for k, c in v["cells"].items() if k[0] == "X"))

    def test_noise_sizes(self):
        seen = {}
        orig = t.t.noise_bar

        def spy(sh, n):
            seen[n] = seen.get(n, 0) + 1
            return orig(sh, n)

        t.t.noise_bar = spy
        try:
            t.verdict(rows_all(p=1e-9))
        finally:
            t.t.noise_bar = orig
        self.assertEqual(seen, {71: 10, 401: 2})

    def test_sum_guard_differs_by_family(self):
        v = t.verdict(rows_all(p=1e-9, dsum=-3.0, se_sum=5.0))    # ΔΣ < 0: E — нет; X — ≥ −SE_Σ — да
        self.assertFalse(v["cells"]["E58_S"]["gates"]["e"])
        self.assertTrue(v["cells"]["X61_S"]["gates"]["e"])
        v = t.verdict(rows_all(p=1e-9, dsum=-6.0, se_sum=5.0))
        self.assertFalse(v["cells"]["X61_S"]["gates"]["e"])

    def test_plateau_fg(self):
        r = rows_all(p=1e-9)
        r["E60_S2"] = row(d=0.1, p=1e-9)                          # сосед слабее 0,5·Δ клетки (0,5)
        v = t.verdict(r)
        self.assertFalse(v["cells"]["E60_S05"]["gates"]["f"])
        self.assertTrue(v["cells"]["E60_L05"]["gates"]["f"])

    def test_dropped_and_inert(self):
        r = rows_all(p=1e-9)
        r["E57_S"] = dict(dropped=True)
        r["E58_L"]["inert"] = True
        v = t.verdict(r)
        self.assertTrue(v["cells"]["E57_S"]["dropped"])
        self.assertFalse(v["cells"]["E57_S"]["passed"])
        self.assertFalse(v["cells"]["E58_L"]["gates"]["b"])       # «не действует» — p = 1

    def test_by_selection_label(self):
        v = t.verdict(rows_all(p=1e-9, by_selection=True))
        self.assertTrue(v["cells"]["E58_S"]["by_selection"])
        self.assertTrue(v["cells"]["X61_S"]["carries_rt_off"])
        self.assertFalse(v["cells"]["E58_S"]["carries_rt_off"])

    def test_months_and_noise_gates(self):
        v = t.verdict(rows_all(p=1e-9, dm=(0.1, 0.1, 0.1, -0.1, -0.1)))
        self.assertFalse(v["cells"]["E58_S"]["gates"]["c"])
        v = t.verdict(rows_all(p=1e-9, sh=0.1))
        self.assertFalse(v["cells"]["E58_S"]["gates"]["g"])


if __name__ == "__main__":
    unittest.main()
