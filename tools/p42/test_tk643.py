#!/usr/bin/env python3
"""Тесты TK-643 (§13.43) на синтетике: сетки, соседи для плато, Холм, правило п. 7, описательный блок D. Без движка и без P&L."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk643_run as t  # noqa: E402


def row(d, p=0.5, n=300, months=(60, 60, 60, 60, 30), dm=(0.1, 0.1, 0.1, 0.1, 0.1), rd=0.1, rc=0.1, sh=1.0, sc=100.0, dsum=10.0, se_sum=5.0):
    return dict(n=n, months=[(m, 0.0) for m in months], delta=dict(d=d, p=p, dsum=dsum, se_sum=se_sum, s_c=sc), dmonths=list(dm), robust_days=rd, robust_coins=rc, sharpe=sh)


class Grid(unittest.TestCase):
    def test_counts(self):
        for side in "LS":
            c = t.block_cells(side)
            self.assertEqual((len(c["A"]), len(c["B"]), len(c["C"])), (63, 34, 215))
            ids = [x["cid"] for b in c.values() for x in b]
            self.assertEqual(len(ids), len(set(ids)))

    def test_base_excluded(self):
        c = t.block_cells("L")
        self.assertNotIn("A_s5_none", [x["cid"] for x in c["A"]])
        self.assertNotIn("B_rf0.2_rs20_off", [x["cid"] for x in c["B"]])
        self.assertIn("B_rf0.2_rs2_tt20_tb0.1", [x["cid"] for x in c["B"]])
        s = t.block_cells("S")
        self.assertNotIn("B_rf0.2_rs2_tt20_tb0.1", [x["cid"] for x in s["B"]])
        self.assertNotIn("C_bm1_rr2_tj0_t_high5_q50", [x["cid"] for x in s["C"]])
        self.assertIn("C_bm0_rr2_tj0_t_high5_q50", [x["cid"] for x in s["C"]])

    def test_patch_off(self):
        c = {x["cid"]: x for x in t.block_cells("S")["B"]}
        self.assertEqual(c["B_off_off"]["patch"], {"rf": 0, "tt": 0})
        self.assertEqual(t.effective_on("S", c["B_off_off"]["patch"]), (False, False))
        self.assertEqual(t.effective_on("L", {}), (True, False))
        self.assertEqual(t.effective_on("S", {}), (True, True))
        self.assertEqual(t.patch_str({"s": 1.5, "ls": 0.5}), "s=1.5 ls=0.5")


class Neighbours(unittest.TestCase):
    def setUp(self):
        self.c = {s: t.block_cells(s) for s in "LS"}
        self.idx = {s: t.ax_index(self.c[s]) for s in "LS"}
        self.by = {s: {x["cid"]: x for b in self.c[s].values() for x in b} for s in "LS"}

    def nb(self, side, cid):
        return t.neighbours(side, self.by[side][cid], self.idx[side])

    def test_s_skips_base(self):
        self.assertEqual(self.nb("L", "A_s4_none"), {"s": ["A_s3_none", "A_s6_none"]})
        self.assertEqual(self.nb("L", "A_s6_none"), {"s": ["A_s4_none", "A_s8_none"]})
        self.assertEqual(self.nb("L", "A_s5_c3"), {"s": ["A_s4_c3", "A_s6_c3"]})
        self.assertEqual(self.nb("L", "A_s12_ls1"), {"s": ["A_s8_ls1"]})

    def test_b(self):
        n = self.nb("L", "B_rf0.2_rs20_tt5_tb0.1")
        self.assertEqual(n["rf"], ["B_rf0.1_rs20_tt5_tb0.1", "B_rf0.3_rs20_tt5_tb0.1"])
        self.assertEqual(n["rs"], ["B_rf0.2_rs2_tt5_tb0.1"])
        self.assertEqual(n["tt"], ["B_rf0.2_rs20_tt20_tb0.1"])
        self.assertEqual(n["tb"], ["B_rf0.2_rs20_tt5_tb0.3"])
        # база L — rf0.2_rs20 / tx выкл.: у B_rf0.1_rs20_off сосед по rf вправо — база, берётся 0,3
        self.assertEqual(self.nb("L", "B_rf0.1_rs20_off"), {"rf": ["B_rf0.3_rs20_off"], "rs": ["B_rf0.1_rs2_off"]})
        self.assertEqual(self.nb("L", "B_off_off"), {})

    def test_c(self):
        n = self.nb("L", "C_bm0_rr1_tj0_t_high5_q50")
        self.assertEqual(n, {"rr": ["C_bm0_rr3_tj0_t_high5_q50"], "tj": ["C_bm0_rr1_tj0.25_t_high5_q50"],
                             "t_high": ["C_bm0_rr1_tj0_t_high3_q50", "C_bm0_rr1_tj0_t_high8_q50"], "q": ["C_bm0_rr1_tj0_t_high5_q25", "C_bm0_rr1_tj0_t_high5_q75"]})
        self.assertEqual(self.nb("L", "C_bm0_rr3_tj0_t_high5_q50"), {"rr": ["C_bm0_rr1_tj0_t_high5_q50"], "tj": ["C_bm0_rr3_tj0.25_t_high5_q50"],
                         "t_high": ["C_bm0_rr3_tj0_t_high3_q50", "C_bm0_rr3_tj0_t_high8_q50"], "q": ["C_bm0_rr3_tj0_t_high5_q25", "C_bm0_rr3_tj0_t_high5_q75"]})
        n = self.nb("S", "C_bm2_rr2_tj0.25_t_high5_q50")
        self.assertEqual(n["tj"], ["C_bm2_rr2_tj0_t_high5_q50"])
        self.assertEqual(n["t_high"], ["C_bm2_rr2_tj0.25_t_high3_q50", "C_bm2_rr2_tj0.25_t_high8_q50"])

    def test_plateau(self):
        self.assertTrue(t.plateau_ok(1.0, {"s": ["a", "b"]}, {"a": 0.5, "b": 0.9}))
        self.assertFalse(t.plateau_ok(1.0, {"s": ["a", "b"]}, {"a": 0.49, "b": 0.9}))
        self.assertFalse(t.plateau_ok(1.0, {}, {}))


class Rule(unittest.TestCase):
    def test_holm(self):
        self.assertEqual(t.holm([0.01, 0.02, 0.5]), [True, True, False])
        self.assertEqual(t.holm([0.02, 0.03, 0.5]), [False, False, False])
        self.assertEqual(t.holm([0.001, 0.02, 0.03], alpha=0.1), [True, True, True])

    def test_verdict_none_and_one(self):
        cells = t.block_cells("L")
        ids = [x["cid"] for b in cells.values() for x in b]
        rows = {c: row(0.0, p=0.5) for c in ids}
        v = t.verdict_side("L", rows, cells)
        self.assertEqual(v["m"], 312)
        self.assertEqual(v["trials_noise"], 392)
        self.assertEqual(v["passed"], [])
        self.assertTrue(v["answer"].startswith("нет"))
        # одна сильная клетка с плато и шумом
        rows["A_s4_none"] = row(1.0, p=1e-5, sh=9.0)
        rows["A_s3_none"] = row(0.6)
        rows["A_s6_none"] = row(0.6)
        v = t.verdict_side("L", rows, cells)
        self.assertEqual(v["passed"], ["A_s4_none"])
        # плато не держит
        rows["A_s6_none"] = row(0.4)
        self.assertEqual(t.verdict_side("L", rows, cells)["passed"], [])
        rows["A_s6_none"] = row(0.6)
        # мало сделок в ноябре
        rows["A_s4_none"] = row(1.0, p=1e-5, sh=9.0, months=(60, 60, 60, 60, 7))
        self.assertEqual(t.verdict_side("L", rows, cells)["passed"], [])
        # 3 из 5 месяцев
        rows["A_s4_none"] = row(1.0, p=1e-5, sh=9.0, dm=(0.1, 0.1, 0.1, -0.1, -0.1))
        self.assertEqual(t.verdict_side("L", rows, cells)["passed"], [])
        # охрана суммы
        rows["A_s4_none"] = row(1.0, p=1e-5, sh=9.0, dsum=-6.0, se_sum=5.0)
        self.assertEqual(t.verdict_side("L", rows, cells)["passed"], [])
        # шум: Sharpe ниже планки
        rows["A_s4_none"] = row(1.0, p=1e-5, sh=2.0)
        self.assertFalse(t.verdict_side("L", rows, cells)["cells"]["A_s4_none"]["gates"]["g"])


class Cross(unittest.TestCase):
    def test_cross_20(self):
        cells = t.block_cells("L")
        ids = [x["cid"] for b in cells.values() for x in b]
        rows = {c: row(0.0) for c in ids}
        for k, c in enumerate(("A_s3_none", "A_s4_none", "B_off_off", "B_rf0.1_rs2_off", "C_bm2_rr2_tj0_t_high5_q50", "C_bm3_rr2_tj0_t_high5_q50")):
            rows[c] = row(1.0 - 0.1 * (k % 2))
        rows["A_s6_none"] = row(5.0, n=50)         # без минимума сделок — не берётся
        d = t.cross_cells(cells, rows)
        self.assertEqual(len(d), 20)
        self.assertTrue(all(len(x["parts"]) >= 2 for x in d))
        self.assertFalse(any("A_s6_none" in x["parts"] for x in d))
        x = [x for x in d if x["parts"] == ["A_s3_none", "B_off_off", "C_bm2_rr2_tj0_t_high5_q50"]][0]
        self.assertEqual(x["patch"], {"s": 3, "rf": 0, "tt": 0, "bm": 2, "rr": 2, "tj": 0, "t_high": 5, "q": 50})


if __name__ == "__main__":
    unittest.main()
