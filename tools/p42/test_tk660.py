#!/usr/bin/env python3
"""Тесты TK-660 (§13.60) на синтетике: клетки, соседи, правило п. 7 (а)–(ж), метка «по построению отбора». Без движка и без P&L."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk660_run as t  # noqa: E402


def row(d, p=0.001, n=300, months=(60, 60, 60, 60, 30), dm=(0.1, 0.1, 0.1, 0.1, 0.1), rd=0.1, rc=0.1, sh=9.0, sc=100.0, dsum=10.0, se_sum=5.0):
    return dict(n=n, months=[(m, 0.0) for m in months], delta=dict(d=d, p=p, dsum=dsum, se_sum=se_sum, s_c=sc), dmonths=list(dm),
                robust_days=rd, robust_coins=rc, sharpe=sh)


def rows_all(f=lambda c: 0.5, base_n=319, **kw):
    r = {c["cid"]: row(f(c), **kw) for c in t.cells()}
    r["_base_n"] = base_n
    return r


class Cells(unittest.TestCase):
    def test_cells(self):
        cs = t.cells()
        self.assertEqual([c["cid"] for c in cs], ["K_k5", "K_k10", "K_k15", "K_k20", "K_k25"])
        self.assertEqual([c["patch"] for c in cs], [{"ek": k} for k in (5, 10, 15, 20, 25)])
        self.assertEqual(t.trials(), 31)

    def test_neighbours_skip_base(self):
        cs = t.cells()
        idx = {c["ax"]["k"]: c["cid"] for c in cs}
        self.assertEqual(t.neighbours(cs[0], idx), {"k": ["K_k10"]})
        self.assertEqual(t.neighbours(cs[2], idx), {"k": ["K_k10", "K_k20"]})
        self.assertEqual(t.neighbours(cs[4], idx), {"k": ["K_k20"]})


class Rule(unittest.TestCase):
    def test_all_pass(self):
        v = t.verdict(rows_all())
        self.assertEqual(len(v["passed"]), 5)
        self.assertTrue(v["answer"].startswith("кандидат вперёд"))

    def test_sum_guard_strict(self):
        v = t.verdict(rows_all(dsum=0.0))
        self.assertEqual(v["passed"], [])
        self.assertEqual(v["answer"], "нет: база шорта остаётся")
        self.assertFalse(v["cells"]["K_k5"]["gates"]["e"])

    def test_holm_and_plateau(self):
        v = t.verdict(rows_all(p=0.02))                   # 0,02 > 0,05 / 5
        self.assertFalse(any(c["gates"]["b"] for c in v["cells"].values()))
        v = t.verdict(rows_all(f=lambda c: 1.0 if c["ax"]["k"] == 15 else 0.1))
        self.assertFalse(v["cells"]["K_k15"]["gates"]["f"])
        self.assertTrue(v["cells"]["K_k10"]["gates"]["f"])

    def test_months_and_noise(self):
        v = t.verdict(rows_all(dm=(0.1, 0.1, 0.1, -0.1, -0.1)))
        self.assertFalse(v["cells"]["K_k5"]["gates"]["c"])
        v = t.verdict(rows_all(sh=1.0))
        self.assertFalse(v["cells"]["K_k5"]["gates"]["g"])

    def test_by_selection_label(self):
        v = t.verdict(rows_all(n=250))
        self.assertTrue(v["cells"]["K_k5"]["by_selection"])
        v = t.verdict(rows_all(n=330))
        self.assertFalse(v["cells"]["K_k5"]["by_selection"])


if __name__ == "__main__":
    unittest.main()
