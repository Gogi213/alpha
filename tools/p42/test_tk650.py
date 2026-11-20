#!/usr/bin/env python3
"""Тесты TK-650 (§13.50) на синтетике: клетки, лоты (копия `tp_lots_n`), соседи, правило п. 7 (а)–(з), поворот поверхности. Без движка и без P&L."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk650_run as t  # noqa: E402


def row(d, p=0.5, n=300, months=(60, 60, 60, 60, 30), dm=(0.1, 0.1, 0.1, 0.1, 0.1), rd=0.1, rc=0.1, sh=1.0, sc=100.0, dsum=10.0, se_sum=5.0):
    return dict(n=n, months=[(m, 0.0) for m in months], delta=dict(d=d, p=p, dsum=dsum, se_sum=se_sum, s_c=sc), dmonths=list(dm), robust_days=rd, robust_coins=rc, sharpe=sh)


def rows_all(f=lambda c: 0.0, **kw):
    return {c["cid"]: row(f(c), **kw) for c in t.cells()}


class Cells(unittest.TestCase):
    def test_counts(self):
        cs = t.cells()
        self.assertEqual(len(t.family()), 73)
        self.assertEqual(sum(1 for c in cs if c["block"] == "G"), 71)
        self.assertEqual([c["cid"] for c in cs if c["block"] != "G"], ["N_p15", "N_p60", "R_ns1"])
        self.assertEqual(len({c["cid"] for c in cs}), len(cs))
        self.assertNotIn("G_g1_k0", [c["cid"] for c in cs])

    def test_patch(self):
        by = {c["cid"]: c for c in t.cells()}
        self.assertEqual(by["G_g1_k3"]["patch"], {"tpk": 3})
        self.assertEqual(by["G_g0.05_k0"]["patch"], {"tpg": 0.05})
        self.assertEqual(by["G_g0.25_k-0.9"]["patch"], {"tpg": 0.25, "tpk": -0.9})
        self.assertEqual(by["N_p60"]["patch"], {"natr_exit_p": 60})
        self.assertFalse(by["R_ns1"]["m"])


class Lots(unittest.TestCase):
    def test_sum_and_shape(self):
        for k in t.KAPPAS:
            lots = t.tp_lots(k)
            self.assertEqual(sum(lots), t.NOTIONAL_U * t.Q_PCT // 100)
            self.assertTrue(all(x > 0 for x in lots))
        self.assertEqual(t.tp_lots(0), [31_250_000] * 8)
        self.assertLess(t.tp_lots(3)[0], t.tp_lots(3)[-1])
        self.assertGreater(t.tp_lots(-0.75)[0], t.tp_lots(-0.75)[-1])

    def test_min_lot(self):
        self.assertAlmostEqual(t.min_lot_usd(100), 0.61, places=2)
        self.assertAlmostEqual(t.min_lot_usd(30), 1.95, places=2)
        self.assertGreaterEqual(t.min_lot_usd(10), 5.0)
        self.assertGreaterEqual(t.min_lot_usd(-0.9), 5.0)
        ok = {c["cid"]: t.exec_ok(c) for c in t.family()}
        self.assertFalse(ok["G_g1_k30"])
        self.assertFalse(ok["G_g0.5_k100"])
        self.assertTrue(ok["G_g0.5_k10"])
        self.assertTrue(ok["N_p15"])


class Neighbours(unittest.TestCase):
    def setUp(self):
        self.fam = t.family()
        self.idx = t.ax_index(self.fam)
        self.by = {c["cid"]: c for c in self.fam}

    def nb(self, cid):
        return t.neighbours(self.by[cid], self.idx)

    def test_interior(self):
        self.assertEqual(self.nb("G_g0.5_k3"), {"tpg": ["G_g0.25_k3", "G_g1_k3"], "tpk": ["G_g0.5_k1", "G_g0.5_k6"]})

    def test_skip_base(self):
        self.assertEqual(self.nb("G_g0.5_k0")["tpg"], ["G_g0.25_k0", "G_g2_k0"])
        self.assertEqual(self.nb("G_g1_k1")["tpk"], ["G_g1_k-0.75", "G_g1_k3"])

    def test_edge(self):
        self.assertEqual(self.nb("G_g0.05_k100"), {"tpg": ["G_g0.125_k100"], "tpk": ["G_g0.05_k30"]})

    def test_natr(self):
        self.assertEqual(self.nb("N_p15"), {"natr_exit_p": ["N_p60"]})
        self.assertEqual(self.nb("N_p60"), {"natr_exit_p": ["N_p15"]})


class Verdict(unittest.TestCase):
    def test_none(self):
        v = t.verdict_side("L", rows_all())
        self.assertEqual(v["m"], 73)
        self.assertEqual(v["trials_noise"], 343)
        self.assertEqual(v["passed"], [])
        self.assertTrue(v["answer"].startswith("нет"))

    def test_region_passes_and_exec_gate(self):
        good = lambda c: c["block"] == "G" and c["ax"]["tpg"] <= 0.25 and c["ax"]["tpk"] >= 6  # noqa: E731
        r = rows_all(lambda c: 1.0 if good(c) else 0.0)
        for c in t.family():
            if good(c):
                r[c["cid"]].update(row(1.0, p=1e-6, sh=12.0))
        v = t.verdict_side("S", r)
        self.assertIn("G_g0.125_k10", v["passed"])
        self.assertTrue(v["cells"]["G_g0.125_k30"]["gates"]["f"])
        self.assertFalse(v["cells"]["G_g0.125_k30"]["gates"]["h"])   # (з): ступень 1,95 $
        self.assertNotIn("G_g0.125_k30", v["passed"])
        self.assertFalse(v["cells"]["G_g0.25_k10"]["gates"]["f"])    # сосед γ 0,5 — Δ 0

    def test_months_and_min(self):
        r = rows_all()
        r["G_g2_k1"] = row(1.0, p=1e-6, sh=8.0, dm=(0.1, -0.1, -0.1, 0.1, 0.1))
        r["G_g2_k3"] = row(1.0, p=1e-6, sh=8.0, months=(60, 60, 19, 60, 30))
        v = t.verdict_side("L", r)
        self.assertFalse(v["cells"]["G_g2_k1"]["gates"]["c"])
        self.assertFalse(v["cells"]["G_g2_k3"]["gates"]["a"])


class Turn(unittest.TestCase):
    def test_interior(self):
        f = lambda c: -((c["ax"].get("tpg", 1) - 0.5) ** 2) - ((c["ax"].get("tpk", 0) - 3) / 10) ** 2 + 1  # noqa: E731
        tr = t.turn(rows_all(f))
        self.assertEqual(tr["where"], "внутри сетки")
        self.assertEqual((tr["argmax"]["tpg"], tr["argmax"]["tpk"]), (0.5, 3))
        self.assertEqual(tr["old_edge"]["region_n"], 4 * 5)

    def test_far_edge(self):
        tr = t.turn(rows_all(lambda c: (-c["ax"].get("tpg", 1) + c["ax"].get("tpk", 0) / 10)))
        self.assertTrue(tr["where"].startswith("на краю: дальний предел"))

    def test_min_trades_skipped(self):
        r = rows_all(lambda c: 1.0 if c["cid"] == "G_g4_k6" else 0.0)
        r["G_g4_k6"]["n"] = 10
        tr = t.turn(r)
        self.assertNotIn("G_g4_k6", tr["smooth"])


if __name__ == "__main__":
    unittest.main()
