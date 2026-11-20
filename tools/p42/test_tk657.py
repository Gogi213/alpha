#!/usr/bin/env python3
"""Тесты TK-657 (§13.57, §13.57.1) на синтетике: клетки, соседи, правило п. 7 (A1 / B) и A2, разложение, парная ΔΣ. Без движка и без P&L."""
from __future__ import annotations

import os
import sys
import types
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk604_stats as st  # noqa: E402
import tk657_run as t  # noqa: E402


def row(d, p=0.5, n=300, months=(60, 60, 60, 60, 30), dm=(0.1,) * 5, rd=0.1, rc=0.1, sh=1.0, sc=100.0, dsum=10.0, se_sum=5.0, lower=0.0, a2=None):
    r = dict(n=n, months=[(m, 0.0) for m in months], delta=dict(d=d, p=p, dsum=dsum, se_sum=se_sum, s_c=sc, lower=lower),
             dmonths=list(dm), robust_days=rd, robust_coins=rc, sharpe=sh)
    if a2 is not None:
        r["a2"] = a2
    return r


def a2(dsum=0.0, p=0.5, months=(1.0,) * 5, rd=1.0, rc=1.0):
    return dict(dsum=dsum, p=p, months=list(months), robust_days=rd, robust_coins=rc)


def rows_all(f=lambda c: 0.0, g=lambda c: a2(), **kw):
    return {c["cid"]: row(f(c), a2=g(c) if c["block"] == "A" else None, **kw) for c in t.cells()}


def ser(day, val, sym):
    return types.SimpleNamespace(day=np.asarray(day), val=np.asarray(val, dtype=float), sym=np.asarray(sym))


class Cells(unittest.TestCase):
    def test_counts(self):
        cs = t.cells()
        self.assertEqual(len(cs), 20)
        self.assertEqual(sum(1 for c in cs if c["block"] == "A"), 14)
        self.assertEqual(t.trials(), 54)
        self.assertNotIn("A_D_1", [c["cid"] for c in cs])
        self.assertFalse(any(k in c["patch"] for c in cs for k in ("rf", "rs", "rr", "s", "t_high", "q", "tj", "track")))

    def test_patch_matches_tk618(self):
        import json
        fz = json.load(open(os.path.join(os.path.dirname(__file__), "..", "..", "docs", "research", "freeze", "tk618-cells.json"), encoding="utf-8"))
        ref = {c["id"]: {k: float(v) for k, v in c["patch"].items()} for c in fz["cells"]}
        for c in t.cells():
            self.assertEqual({k: float(v) for k, v in c["patch"].items()}, ref[c["cid"]], c["cid"])
        self.assertEqual(t.t.patch_str({"n_bounce": 1}), "n_bounce=1")


class Neighbours(unittest.TestCase):
    def nb(self, cid):
        cs = t.cells()
        return t.neighbours({c["cid"]: c for c in cs}[cid], t.ax_index(cs))

    def test_skip_base(self):
        self.assertEqual(self.nb("A_n_1"), {"n_bounce": ["A_n_3"]})          # 2 — база, берётся 3
        self.assertEqual(self.nb("A_re1"), {"reentry": ["A_re2"]})           # 0 — база, левее нет
        self.assertEqual(self.nb("A_re2"), {"reentry": ["A_re1"]})
        self.assertEqual(self.nb("A_t1h_0.1"), {"t1h": ["A_t1h_0.25"]})
        self.assertEqual(self.nb("A_t1h_0.25"), {"t1h": ["A_t1h_0.1"]})      # 0,5 — база, правее нет
        self.assertEqual(self.nb("B_tlow_2"), {"t_low": ["B_tlow_0.5"]})


class Verdict(unittest.TestCase):
    def test_none(self):
        v = t.verdict(rows_all())
        self.assertEqual((v["passed"], v["answer"], v["m"], v["trials_noise"]), ([], "нет: база остаётся", 20, 54))
        v2 = t.verdict_a2(rows_all())
        self.assertEqual((v2["passed"], v2["m"]), ([], 14))

    def test_pass_b_plateau(self):
        good = {"B_tlow_0.5", "B_tlow_2"}
        r = rows_all(p=0.9)
        for c in good:
            r[c] = row(1.0, p=1e-6, sh=8.0)
        v = t.verdict(r)
        self.assertEqual(sorted(v["passed"]), sorted(good))
        r["B_tlow_0.5"] = row(0.4, p=1e-6, sh=8.0)                         # сосед < 0,5·Δ — плато у B_tlow_2 рвётся
        self.assertEqual(t.verdict(r)["passed"], ["B_tlow_0.5"])          # у B_tlow_0.5 сосед 1,0 ≥ 0,5·0,4 — плато держится
        self.assertFalse(t.verdict(r)["cells"]["B_tlow_2"]["gates"]["f"])

    def test_a2_pass_and_noninferiority(self):
        good = {"A_n_1", "A_n_3"}
        r = rows_all(p=0.9)
        for c in good:
            r[c] = row(-0.05, p=0.9, sh=8.0, lower=-0.08, a2=a2(dsum=100.0, p=1e-6))
        v2 = t.verdict_a2(r)
        self.assertEqual(sorted(v2["passed"]), sorted(good))
        self.assertEqual(t.verdict(r)["passed"], [])                      # A1: Δ $/сделку < 0 — не проходит
        r["A_n_1"]["delta"]["lower"] = -0.11
        self.assertFalse(t.verdict_a2(r)["cells"]["A_n_1"]["gates"]["c"])
        r["A_n_1"]["delta"]["lower"] = -0.08
        r["A_n_1"]["a2"]["months"] = [1, 1, 1, -1, -1]
        self.assertFalse(t.verdict_a2(r)["cells"]["A_n_1"]["gates"]["d"])

    def test_a2_holm_14(self):
        r = rows_all(p=0.9)
        r["A_k_1"] = row(0.0, sh=8.0, a2=a2(dsum=50.0, p=0.05 / 14 * 1.01))
        self.assertFalse(t.verdict_a2(r)["cells"]["A_k_1"]["gates"]["b"])
        r["A_k_1"]["a2"]["p"] = 0.05 / 14 * 0.99
        self.assertTrue(t.verdict_a2(r)["cells"]["A_k_1"]["gates"]["b"])


class Decompose(unittest.TestCase):
    def test_parts(self):
        bk, bp = [("X", 1), ("X", 2), ("Y", 3)], [1.0, 2.0, 3.0]
        d = t.decompose([("X", 1), ("Y", 3), ("Z", 9)], [1.5, 3.0, -4.0], bk, bp)
        self.assertEqual(d, dict(added_n=1, added_sum=-4.0, removed_n=1, removed_sum=2.0, common_n=2, common_dsum=0.5, common_changed=1))
        self.assertFalse(t.inert(d))
        self.assertTrue(t.inert(t.decompose(bk, bp, bk, bp)))


class SumStat(unittest.TestCase):
    def test_sum_and_robust(self):
        rng = np.random.default_rng(1)
        n = st.NDAYS
        bday = np.arange(n)
        b = ser(bday, rng.normal(0, 1, n), bday % 5)
        extra_day = np.arange(0, n, 2)
        c = ser(np.concatenate([bday, extra_day]), np.concatenate([b.val, np.full(len(extra_day), 0.5)]),
                np.concatenate([b.sym, extra_day % 5]))
        s = t.sum_stat(st, c, b, 5)
        self.assertAlmostEqual(s["dsum"], 0.5 * len(extra_day), places=9)
        self.assertLess(s["p"], 0.001)                                     # постоянная добавка — ΔΣ > 0 уверенно
        self.assertAlmostEqual(sum(s["months"]), s["dsum"], places=9)
        self.assertAlmostEqual(s["robust_days"], s["dsum"] - 1.0, places=9)
        top2 = np.sort(np.bincount(extra_day % 5, minlength=5) * 0.5)[-2:].sum()
        self.assertAlmostEqual(s["robust_coins"], s["dsum"] - top2, places=9)
        z = t.sum_stat(st, b, b, 5)
        self.assertEqual(z["dsum"], 0.0)
        self.assertGreater(z["p"], 0.4)


if __name__ == "__main__":
    unittest.main()
