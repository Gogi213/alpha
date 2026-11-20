#!/usr/bin/env python3
"""Тесты TK-649 (§13.49) на синтетике: сетка T, соседи для плато, Холм по 12 клеткам, правило п. 7, сравнение meta.json. Без движка и без P&L."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk649_run as t  # noqa: E402


def row(d, p=0.5, n=300, months=(60, 60, 60, 60, 30), dm=(0.1, 0.1, 0.1, 0.1, 0.1), rd=0.1, rc=0.1, sh=1.0, sc=100.0, dsum=10.0, se_sum=5.0):
    return dict(n=n, months=[(m, 0.0) for m in months], delta=dict(d=d, p=p, dsum=dsum, se_sum=se_sum, s_c=sc), dmonths=list(dm), robust_days=rd, robust_coins=rc, sharpe=sh)


def rows_all(d=0.0, p=0.5, **kw):
    return {s: {c: row(d, p, **kw) for c, _ in t.cells()} for s in t.SIDES}


class Grid(unittest.TestCase):
    def test_cells(self):
        self.assertEqual([c for c, _ in t.cells()], ["tu5", "tu15", "tu30", "tu60", "tu120", "tu240"])

    def test_neighbours(self):
        self.assertEqual(t.neighbours("tu5"), {"T": ["tu15"]})
        self.assertEqual(t.neighbours("tu30"), {"T": ["tu15", "tu60"]})
        self.assertEqual(t.neighbours("tu240"), {"T": ["tu120"]})


class Verdict(unittest.TestCase):
    def test_nothing_passes_on_noise(self):
        v = t.verdict(rows_all(0.0, 0.5))
        for s in t.SIDES:
            self.assertEqual(v[s]["passed"], [])
            self.assertEqual(v[s]["m_holm"], 12)
            self.assertEqual(v[s]["trials_noise"], 6 + 392)
            self.assertTrue(v[s]["answer"].startswith("нет"))

    def test_holm_over_12(self):
        r = rows_all(1.0, 0.5, sh=50.0)
        r["L"]["tu30"]["delta"]["p"] = 0.05 / 12 * 0.99   # проходит первый шаг Холма при m = 12
        v = t.verdict(r)
        self.assertTrue(v["L"]["cells"]["tu30"]["gates"]["b"])
        r["L"]["tu30"]["delta"]["p"] = 0.05 / 12 * 1.01   # не проходит при m = 12 (прошёл бы при m = 6)
        v = t.verdict(r)
        self.assertFalse(v["L"]["cells"]["tu30"]["gates"]["b"])

    def test_full_pass_and_plateau(self):
        r = rows_all(1.0, 0.5, sh=50.0)
        r["S"]["tu60"]["delta"]["p"] = 1e-6
        v = t.verdict(r)
        self.assertEqual(v["S"]["passed"], ["tu60"])
        r["S"]["tu120"]["delta"]["d"] = 0.4                 # сосед < 0,5·Δ — плато нет
        v = t.verdict(r)
        self.assertFalse(v["S"]["cells"]["tu60"]["gates"]["f"])
        self.assertEqual(v["S"]["passed"], [])

    def test_min_trades_and_months(self):
        r = rows_all(1.0, 0.5, sh=50.0)
        r["L"]["tu15"]["delta"]["p"] = 1e-6
        r["L"]["tu15"]["months"][4] = (7, 0.0)
        v = t.verdict(r)
        self.assertFalse(v["L"]["cells"]["tu15"]["gates"]["a"])
        r = rows_all(1.0, 0.5, sh=50.0)
        r["L"]["tu15"]["dmonths"] = [0.1, 0.1, 0.1, -0.1, -0.1]
        self.assertFalse(t.verdict(r)["L"]["cells"]["tu15"]["gates"]["c"])


class Meta(unittest.TestCase):
    def _w(self, d, obj):
        p = os.path.join(d, "m%d.json" % len(os.listdir(d)))
        json.dump(obj, open(p, "w", encoding="utf-8"))
        return p

    def test_meta(self):
        with tempfile.TemporaryDirectory() as d:
            old = self._w(d, dict(id="L_base", long=dict(trades=5), params="X { tj_m: 0 }, port: Y"))
            self.assertTrue(t.meta_equal(self._w(d, dict(id="L_base", long=dict(trades=5), params="X { tj_m: 0 }, port: Y")), old)[0])
            self.assertTrue(t.meta_equal(self._w(d, dict(id="L_base", long=dict(trades=5), params="X { tj_m: 0, tl: 0 }, port: Y")), old)[0])
            self.assertFalse(t.meta_equal(self._w(d, dict(id="L_base", long=dict(trades=5), params="X { tj_m: 0, tl: 1 }, port: Y")), old)[0])
            self.assertFalse(t.meta_equal(self._w(d, dict(id="L_base", long=dict(trades=6), params="X { tj_m: 0 }, port: Y")), old)[0])
            self.assertFalse(t.meta_equal(self._w(d, dict(id="L_base", long=dict(trades=5), params="X { tj_m: 0, tl: 0, tu_min: 5 }, port: Y")), old)[0])


if __name__ == "__main__":
    unittest.main()
