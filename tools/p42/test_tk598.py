"""Юнит-тесты П-42 §13.23 (TK-598): срезы окон, номинал, метки по обучающей части, блоки CV с purge, бутстреп по суткам, Холм, вердикты, объединённая серия, сквозной прогон подгонки на синтетике.
Запуск на srv: cd /data/p42-tk598/py && /data/tk592/venv/bin/python -m unittest test_tk598 (numpy, pandas, catboost, scipy)."""
import json
import math
import os
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk598_fit as FIT  # noqa: E402
import tk598_lib as L  # noqa: E402
import tk598_read as RD  # noqa: E402

DAY = L.DAY


class WindowTests(unittest.TestCase):
    def test_window_mask_edges(self):
        d = np.array([13, 14, 73, 74])
        self.assertEqual(L.window_mask(d, "F").tolist(), [False, True, True, False])
        d = np.array([13, 14, 44, 45])
        self.assertEqual(L.window_mask(d, "jul").tolist(), [False, True, True, False])
        d = np.array([13, 14, 16, 17])
        self.assertEqual(L.window_mask(d, "hold").tolist(), [False, True, True, False])

    def test_blocks(self):
        d = np.array([13, 14, 25, 26, 37, 38, 49, 50, 61, 62, 73, 74])
        self.assertEqual(L.block_of(d).tolist(), [-1, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, -1])

    def test_day_series(self):
        sa, ca, ss, cs = L.day_series(np.array([14, 14, 15, 20]), np.array([1.0, 2.0, 3.0, 9.0]), np.array([True, False, True, False]), 3, 14)
        self.assertEqual(sa.tolist(), [3.0, 3.0, 0.0])
        self.assertEqual(ca.tolist(), [2.0, 1.0, 0.0])
        self.assertEqual(ss.tolist(), [1.0, 3.0, 0.0])
        self.assertEqual(cs.tolist(), [1.0, 1.0, 0.0])


class PurgeTests(unittest.TestCase):
    def test_purge_neighbours_and_overlap(self):
        # проверочный блок суток 26…37: соседние сутки 25 и 38 удаляются; сделка суток 20, чей выход заходит в сутки 26, удаляется; сделка суток 24, выходящая в сутки 24, остаётся
        day = np.array([20, 24, 25, 38, 39, 45, 26])
        entry = np.array([20 * DAY + 5, 24 * DAY + 5, 25 * DAY + 5, 38 * DAY + 5, 39 * DAY + 5, 45 * DAY, 26 * DAY])
        exit_ = np.array([26 * DAY + 100, 24 * DAY + 100, 25 * DAY + 100, 38 * DAY + 100, 39 * DAY + 100, 45 * DAY + 10, 26 * DAY + 10])
        keep = L.purge_train(day, entry, exit_, (26, 37))
        self.assertEqual(sorted(keep.tolist()), [1, 4, 5])

    def test_purge_extended_block_overlap(self):
        # блок 26…37 расширен на 1 сутки: [25·DAY, 39·DAY); сделка суток 24, выходящая в сутки 25 (пересекает расширенный блок), удаляется
        keep = L.purge_train(np.array([24]), np.array([24 * DAY + 80_000_000]), np.array([25 * DAY + 5]), (26, 37))
        self.assertEqual(len(keep), 0)
        keep = L.purge_train(np.array([24]), np.array([24 * DAY + 5]), np.array([24 * DAY + 500]), (26, 37))
        self.assertEqual(keep.tolist(), [0])

    def test_val_rows_never_in_train(self):
        rng = np.random.default_rng(1)
        day = rng.integers(14, 74, 2000)
        entry = day * DAY + rng.integers(0, DAY, 2000)
        exit_ = entry + rng.integers(0, 3 * DAY, 2000)
        for k, blk in enumerate(L.F_BLOCKS):
            tr = L.purge_train(day, entry, exit_, blk)
            self.assertFalse(((day[tr] >= blk[0] - 1) & (day[tr] <= blk[1] + 1)).any())
            lo, hi = (blk[0] - 1) * DAY, (blk[1] + 2) * DAY
            self.assertFalse(((exit_[tr] >= lo) & (entry[tr] < hi)).any())


class LabelTests(unittest.TestCase):
    def test_nominal_and_net_identity(self):
        long_r = {"legs": [[1, 2, 31_250_000, True]] * 8 + [[3, 4, 250_000_000, False]]}
        self.assertEqual(L.nominal_usd(long_r), 500.0)
        short_one = {"legs": [[1, 2, 15_625_000, True]] * 4 + [[3, 4, 187_500_000, False]]}
        self.assertEqual(L.nominal_usd(short_one), 250.0)
        short_two = dict(short_one, lot2={"legs": [[1, 2, 15_625_000, True]] * 4 + [[3, 4, 187_500_000, False]]})
        self.assertEqual(L.nominal_usd(short_two), 500.0)

    def test_trade_frame_and_labels(self):
        with tempfile.TemporaryDirectory() as td:
            tr = pd.DataFrame({"sym": ["A", "B"], "level": [1, 2], "sig_min": [1, 2], "entry_min": [1, 2], "exit_min": [2, 3], "entry_px_e8": [1, 1], "kind": ["stop", "trail"],
                               "takes": [0, 2], "gross": [-5.0, 3.0], "fees": [0.3, 0.2], "funding": [0.1, 0.0], "net": [-5.2, 2.8], "entry_day": [0, 1],
                               "sig_ms": [14 * DAY + 5, 15 * DAY + 5], "entry_ms": [14 * DAY + 9, 15 * DAY + 9], "exit_ms": [14 * DAY + 99, 15 * DAY + 99]})
            tr.to_csv(os.path.join(td, "t.csv"), index=False)
            det = [{"sym": "A", "level": 1, "sig_ms": 14 * DAY + 5, "natr_e8": 400000, "legs": [[1, 1, 500_000_000, False]]},
                   {"sym": "B", "level": 2, "sig_ms": 15 * DAY + 5, "natr_e8": 400000, "legs": [[1, 1, 250_000_000, False]], "lot2": {"legs": [[1, 1, 250_000_000, False]]}}]
            with open(os.path.join(td, "d.jsonl"), "w") as f:
                for r in det:
                    f.write(json.dumps(r) + "\n")
            tf = L.add_labels(L.trade_frame(os.path.join(td, "t.csv"), os.path.join(td, "d.jsonl")))
            self.assertEqual(tf["net_nf"].round(6).tolist(), [-5.3, 2.8])
            self.assertEqual(tf["bps"].round(6).tolist(), [round(-5.3 / 500 * 1e4, 6), round(2.8 / 500 * 1e4, 6)])
            self.assertEqual(tf["y_win"].tolist(), [0, 1])
            self.assertEqual(tf["y_stop"].tolist(), [1, 0])
            self.assertEqual(tf["day"].tolist(), [14, 15])
            # net − (gross − fees) = funding
            self.assertTrue(np.allclose(tf["net"] - tf["net_nf"], tf["funding"], atol=1e-9))

    def test_quintile_ties_into_one_bin(self):
        rel = L.quintile_rel(np.zeros(10), L.quintile_bounds(np.zeros(10)))
        self.assertEqual(set(rel.tolist()), {0})
        y = np.arange(100, dtype=float)
        b = L.quintile_bounds(y)
        rel = L.quintile_rel(y, b)
        self.assertEqual(rel.max(), 4)
        self.assertTrue(np.all(np.diff(rel) >= 0))

    def test_clip_and_weight_norm(self):
        y = np.arange(101, dtype=float)
        lo, hi = L.clip_bounds(y)
        self.assertAlmostEqual(lo, 1.0)
        self.assertAlmostEqual(hi, 99.0)
        w = L.weight_norm(np.array([1.0, 3.0]))
        self.assertAlmostEqual(float(w.mean()), 1.0)


class StatsTests(unittest.TestCase):
    def test_point_stats(self):
        net = np.array([1.0, 2.0, 3.0, -4.0])
        sel = np.array([True, True, False, False])
        r = L.point_stats(net, sel, 2)
        self.assertAlmostEqual(r["mean_all"], 0.5)
        self.assertAlmostEqual(r["mean_sel"], 1.5)
        self.assertAlmostEqual(r["S1"], 1.0)
        self.assertAlmostEqual(r["S2"], 1.5)
        self.assertAlmostEqual(r["S3"], 1.0)

    def test_pick_selected_and_threshold(self):
        s = np.arange(9, dtype=float)
        q = L.oof_threshold(s)
        self.assertAlmostEqual(q, np.quantile(s, 2 / 3))
        self.assertEqual(int(L.pick_selected(s, q).sum()), 3)
        self.assertEqual(int(L.pick_selected(s, L.oof_threshold(s, True), lower=True).sum()), 3)
        self.assertFalse(L.pick_selected(np.array([np.nan]), 0.0)[0])

    def test_boot_indices_blocks_and_determinism(self):
        idx = L.boot_indices(62, b=200)
        self.assertEqual(idx.shape, (200, 62))
        self.assertTrue(idx.min() >= 0 and idx.max() <= 61)
        self.assertTrue(np.array_equal(idx, L.boot_indices(62, b=200)))
        # внутри блока из 4 суток индексы идут подряд
        first = idx[:, :4]
        self.assertTrue(np.all(np.diff(first, axis=1) == 1))
        self.assertFalse(np.array_equal(idx, L.boot_indices(62, b=200, seed=1)))

    def test_boot_stats_constant_series(self):
        nd = 20
        ca = np.full(nd, 5.0)
        sa = np.full(nd, 5.0)      # все сделки по +1
        cs = np.full(nd, 2.0)
        ss = np.full(nd, 4.0)      # выбранные по +2
        s1, s2 = L.boot_stats(sa, ca, ss, cs, nd, b=500)
        self.assertTrue(np.allclose(s1, 1.0))
        self.assertTrue(np.allclose(s2, 4.0))
        self.assertAlmostEqual(L.p_one_sided(s1, "le0"), 1 / 501)
        self.assertAlmostEqual(L.p_one_sided(s1, "ge0"), 1.0)

    def test_p_value_formula(self):
        x = np.array([-1.0, 0.0, 1.0, 2.0])
        self.assertAlmostEqual(L.p_one_sided(x, "le0"), (2 + 1) / 5)

    def test_holm(self):
        self.assertEqual(L.holm_reject([0.001, 0.02, 0.04]), [True, True, True])
        self.assertEqual(L.holm_reject([0.04, 0.01, 0.03]), [False, True, False])     # 0.01 ≤ 0,05/3; 0,03 > 0,05/2
        self.assertEqual(L.holm_reject([0.049]), [True])
        self.assertEqual(L.holm_reject([0.06]), [False])
        self.assertEqual(L.holm_reject([]), [])

    def test_drop_top(self):
        net = np.array([10.0, 5.0, 1.0, 1.0, -3.0])
        sel = np.array([True, True, True, True, True])
        self.assertAlmostEqual(L.drop_top_sum(net, sel, np.array([0, 1, 2, 3, 4]), 2), 1.0 + 1.0 - 3.0)
        self.assertAlmostEqual(L.drop_top_sum(net, sel, np.array(["a", "a", "b", "c", "c"]), 2), -2.0)

    def test_mde(self):
        a = L.mde_s0(10.0, 100, 1)
        b = L.mde_s0(10.0, 100, 4)
        self.assertAlmostEqual(a, (1.6448536 + 0.84) * 1.0, places=3)
        self.assertGreater(b, a)
        # лифт: множитель √(1 − 1/3) относительно MDE среднего выбранных; при f = 0 совпадает с S0
        self.assertAlmostEqual(L.mde_lift(10.0, 100, 1), a * math.sqrt(2 / 3), places=9)
        self.assertAlmostEqual(L.mde_lift(10.0, 100, 4), b * math.sqrt(2 / 3), places=9)
        self.assertAlmostEqual(L.mde_lift(10.0, 100, 1, f=0.0), a, places=9)
        # §13.23.3 п. 2 / п. 4, F-U1: лонг σ = 8,244, n_выб ≈ 88 без Холма → 1,79 $/сд. (лифт), 2,19 (S0); допуск — n_выб в протоколе округлён
        self.assertAlmostEqual(L.mde_lift(8.244, 88, 1), 1.79, delta=0.02)
        self.assertAlmostEqual(L.mde_s0(8.244, 88, 1), 2.19, delta=0.02)
        self.assertTrue(math.isnan(L.mde_lift(8.244, 0, 1)))

    def test_auc_spearman(self):
        self.assertAlmostEqual(L.auc([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9]), 1.0)
        self.assertAlmostEqual(L.auc([0, 1], [0.5, 0.5]), 0.5)
        self.assertTrue(np.isnan(L.auc([1, 1], [0.1, 0.2])))
        self.assertAlmostEqual(L.spearman([1, 2, 3, 4], [10, 20, 30, 40]), 1.0)


def synth_rows(n_days, per_day, effect, seed, d_first=14):
    rng = np.random.default_rng(seed)
    rows = []
    for d in range(d_first, d_first + n_days):
        for i in range(per_day):
            x = rng.normal()
            net = effect * x + rng.normal(0, 5)
            rows.append({"sym": f"C{rng.integers(0, 15)}", "level": i, "sig_ms": d * DAY + i, "day": d, "net_nf": net, "y_win": int(net > 0), "y_stop": 0, "bps": net,
                         "mfe_natr": 1.0, "mae_natr": 1.0, "natr_bp": 50.0, "s_T1": x})
    return pd.DataFrame(rows)


class VerdictTests(unittest.TestCase):
    def fs(self, ts):
        return {"targets": {t: {"q": 0.4, "lower": False} for t in ts}, "pass": list(ts), "m": len(ts), "sigma_net_U1": 5.0}

    def pooled(self, effect, per_day=3, seed=1):
        j = synth_rows(31, per_day, effect, seed)
        a = synth_rows(31, per_day, effect, seed + 100)
        j["win"], a["win"] = "jul", "aug"
        j["pday"] = j["day"] - 14
        a["pday"] = 31 + a["day"] - 14
        return pd.concat([j, a], ignore_index=True)

    def test_confirmed_for_strong_effect(self):
        df = self.pooled(effect=8.0, per_day=4)
        idx = L.boot_indices(62, b=2000)
        r = RD.verdicts(df, self.fs(["T1"]), ["T1"], idx, 5.0)["T1"]
        self.assertGreaterEqual(r["n_sel"], L.MIN_SEL)
        self.assertTrue(r["S1_jul_pos"] and r["S1_aug_pos"])
        self.assertEqual(r["verdict"], "подтверждено")

    def test_not_clear_for_noise(self):
        df = self.pooled(effect=0.0, per_day=4, seed=7)
        idx = L.boot_indices(62, b=2000)
        r = RD.verdicts(df, self.fs(["T1"]), ["T1"], idx, 5.0)["T1"]
        self.assertIn(r["verdict"], ("не ясно", "опровергнуто"))
        self.assertNotEqual(r["verdict"], "подтверждено")

    def test_undefined_when_few_selected(self):
        df = self.pooled(effect=8.0, per_day=1)
        idx = L.boot_indices(62, b=500)
        r = RD.verdicts(df, self.fs(["T1"]), ["T1"], idx, 5.0)["T1"]
        self.assertLess(r["n_sel"], L.MIN_SEL)
        self.assertTrue(r["verdict"].startswith("не определена"))

    def test_refuted_for_negative_effect(self):
        df = self.pooled(effect=-8.0, per_day=4, seed=3)
        idx = L.boot_indices(62, b=2000)
        r = RD.verdicts(df, self.fs(["T1"]), ["T1"], idx, 5.0)["T1"]
        self.assertEqual(r["verdict"], "опровергнуто")

    def test_pooled_day_index(self):
        with tempfile.TemporaryDirectory() as td:
            for w, d in (("jul", 14), ("aug", 14)):
                pd.DataFrame({"sym": ["A"], "level": [1], "sig_ms": [d * DAY], "day": [d], "net_nf": [1.0]}).to_parquet(os.path.join(td, f"rows-{w}-long.parquet"))
            pd.DataFrame({"sym": ["A"], "level": [1], "sig_ms": [44 * DAY], "day": [44], "net_nf": [1.0]}).to_parquet(os.path.join(td, "rows-aug-long.parquet"))
            df = RD.pooled_rows(td, "long")
            self.assertEqual(df["pday"].tolist(), [0, 61])


class FitSmokeTests(unittest.TestCase):
    """Сквозной прогон подгонки на синтетике: сетка из 2 клеток, ≤ 40 итераций; проверяются файлы, ворота и заморозка."""

    def make_tab(self, n, seed, short):
        rng = np.random.default_rng(seed)
        day = rng.integers(14, 74, n)
        entry = day * DAY + rng.integers(0, DAY // 2, n)
        exit_ = entry + rng.integers(60_000, 3 * 3_600_000, n)
        X = rng.normal(size=(n, len(L.FEATS)))
        net = 2.0 * X[:, 0] + rng.normal(0, 4, n)
        t = pd.DataFrame({"sym": [f"C{i % 40}" for i in range(n)], "level": np.arange(n), "sig_ms": day * DAY + np.arange(n), "day": day, "entry_ms": entry, "exit_ms": exit_,
                          "entry_day_abs": entry // DAY, "kind": np.where(rng.random(n) < 0.1, "stop", "trail"), "net_nf": net, "gross": net + 0.3, "fees": 0.3,
                          "nominal": np.where((rng.random(n) < 0.3) & short, 250.0, 500.0),
                          "mfe_natr": np.abs(net) / 3 + 0.1, "mae_natr": np.abs(rng.normal(size=n)) + 0.1})
        t["bps"] = t["net_nf"] / t["nominal"] * 1e4
        t["y_win"] = (t["net_nf"] > 0).astype(np.int8)
        t["y_stop"] = (t["kind"] == "stop").astype(np.int8)
        for i, f in enumerate(L.FEATS):
            t[f] = X[:, i]
        return t

    def test_fit_and_freeze(self):
        FIT.ITER_CAP = 40
        saved = L.GRID
        L.GRID = [(1, 50, 3), (2, 50, 10)]
        try:
            with tempfile.TemporaryDirectory() as td:
                u0 = self.make_tab(3200, 1, short=True)
                u1 = self.make_tab(400, 2, short=True)
                p0, p1 = os.path.join(td, "u0.parquet"), os.path.join(td, "u1.parquet")
                u0.to_parquet(p0)
                u1.to_parquet(p1)
                res = FIT.fit_side("short", p0, p1, os.path.join(td, "fit"), 2)
                self.assertEqual(set(res["targets"]), set(L.TARGETS["short"]))
                for t, td_ in res["targets"].items():
                    self.assertIn("q", td_)
                    for sub, sd in td_["subs"].items():
                        self.assertEqual(len(sd["cv"]), 2)
                        self.assertGreaterEqual(sd["iterations"], FIT.MIN_ITERS)
                        self.assertTrue(os.path.exists(os.path.join(td, "fit", "models", f"short_{sub}_f4.cbm")))
                        self.assertTrue(os.path.exists(os.path.join(td, "fit", "models", f"short_{sub}_final.cbm")))
                # OOF-предсказания: у каждой строки U1 есть значение; эффект признака 0 даёт положительный лифт у T1
                oof = pd.read_parquet(os.path.join(td, "fit", "oof-short.parquet"))
                self.assertEqual(len(oof), len(u1))
                self.assertFalse(oof["s_T1"].isna().any())
                self.assertTrue(res["targets"]["T1"]["gate_oof"])
                fr = FIT.freeze(os.path.join(td, "fit"), os.path.join(td, "freeze"))
                self.assertIn("T1", fr["sides"]["short"]["pass"])
                self.assertEqual(fr["sides"]["short"]["m"], len(fr["sides"]["short"]["pass"]))
                # чтение моделей: sha256 проверяется, ŝ строится
                models = RD.load_models(fr, os.path.join(td, "freeze", "models"))
                s = RD.score(models, "short", "T1", u1[L.FEATS].to_numpy(np.float64))
                self.assertEqual(len(s), len(u1))
                self.assertGreater(L.spearman(s, u1["net_nf"].to_numpy()), 0.3)
                # повреждение файла модели → отказ
                fn = fr["sides"]["short"]["targets"]["T1"]["subs"]["net"]["folds"][0]["file"]
                with open(os.path.join(td, "freeze", "models", fn), "ab") as f:
                    f.write(b"x")
                with self.assertRaises(SystemExit):
                    RD.load_models(fr, os.path.join(td, "freeze", "models"))
        finally:
            L.GRID = saved

    def test_sealed_days(self):
        with tempfile.TemporaryDirectory() as td:
            u0 = self.make_tab(3200, 1, short=False)
            u0.loc[0, "day"] = 74
            p0 = os.path.join(td, "u0.parquet")
            u0.to_parquet(p0)
            u0.loc[0, "day"] = 30
            u1 = os.path.join(td, "u1.parquet")
            u0.to_parquet(u1)
            with self.assertRaises(SystemExit):
                FIT.load_side(p0, u1)


if __name__ == "__main__":
    unittest.main()
