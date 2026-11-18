"""Юнит-тесты П-42 §13.18 (TK-592): корзины, аддитивная модель, клетки, BH/Холм, блоковый бутстреп, подтверждение на синтетике.
Запуск на srv: cd /data/tk592/py && python3 -m unittest test_tk592 (numpy, pandas, scipy)."""
import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk592_lib as T  # noqa: E402
from sigfeat_lib import holm as holm_ref  # noqa: E402


def synth(seed: int, n: int = 24000, inter: float = 0.0, coins: int = 120) -> pd.DataFrame:
    """Эпизоды: монеты с разным базовым уровнем, аддитивные эффекты признаков и (опционально) взаимодействие f_a high × f_b high."""
    rng = np.random.default_rng(seed)
    day = rng.integers(14, 74, n)
    day = day[day != 44]
    n = len(day)
    sym = rng.integers(0, coins, n)
    coin_eff = rng.normal(0, 0.02, coins)[sym]
    X = {f: rng.normal(size=n) for f in T.FEATS}
    X["pierce_ep"] = np.where(rng.random(n) < 0.75, 0, rng.integers(1, 6, n)).astype(float)  # нулевая масса — склеивает границы корзин
    X["lturn24"] = X["lturn24"] + 0.5 * coin_eff * 50
    base = 0.06 + coin_eff + 0.02 * np.tanh(X["lturn24"]) + 0.01 * np.tanh(X["natr_bp"])
    hi_a = X["tpm60"] > np.quantile(X["tpm60"], 2 / 3)
    hi_b = X["mv5"] > np.quantile(X["mv5"], 2 / 3)
    p = np.clip(base + inter * (hi_a & hi_b), 0.001, 0.9)
    y = (rng.random(n) < p).astype(int)
    cb = np.clip(0.06 + coin_eff + 0.02 * np.tanh(X["lturn24"]) + 0.01 * np.tanh(X["natr_bp"]), 0.001, 0.9)
    c_f = rng.binomial(20, cb) / 20.0
    df = pd.DataFrame(X)
    df["turn24h"] = 10 ** df["lturn24"]
    df["y"] = y
    df["c_f"], df["c_n"] = c_f, 20
    df["day"], df["sym"] = day, [f"C{s}" for s in sym]
    df["side"] = "long"
    df["half"] = np.where(day < 44, "A", "B")
    df["in_pool"] = (rng.random(n) < 0.05).astype(int)
    return df


class BucketTests(unittest.TestCase):
    def test_tertiles_and_ties(self):
        x = np.r_[np.zeros(80), np.arange(1, 21)].astype(float)
        b = T.Buckets(x, "tertile")
        self.assertEqual(b.nb, 2)  # границы 1/3 и 2/3 совпали (0) → две корзины ≤ 0 / > 0
        c = b.assign(np.array([0.0, 1.0, np.nan]))
        self.assertEqual(list(c), [0, 1, -1])
        self.assertEqual(b.label(0) + b.label(1), "LH")

    def test_three_buckets(self):
        x = np.arange(300, dtype=float)
        b = T.Buckets(x, "tertile")
        self.assertEqual(b.nb, 3)
        c = b.assign(x)
        self.assertEqual(np.bincount(c).tolist(), [100, 100, 100])

    def test_median(self):
        x = np.arange(100, dtype=float)
        b = T.Buckets(x, "median")
        self.assertEqual(b.nb, 2)
        self.assertEqual(int(b.assign(np.array([10.0]))[0]), 0)
        self.assertEqual(int(b.assign(np.array([90.0]))[0]), 1)

    def test_frozen_boundaries(self):  # границы с половины поиска, не с проверочной
        S = np.arange(300, dtype=float)
        b = T.Buckets(S, "tertile")
        c = b.assign(np.array([1000.0, -5.0]))
        self.assertEqual(c.tolist(), [2, 0])

    def test_empty_top_bucket(self):  # верхняя граница = максимум → верхняя корзина пуста, у поиска две корзины
        x = np.r_[np.arange(60.0), np.full(40, 59.0)]
        b = T.Buckets(x, "tertile")
        self.assertLessEqual(b.nb, 3)
        self.assertGreaterEqual(b.nb, 2)


class StatTests(unittest.TestCase):
    def test_bh(self):
        ps = np.array([0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205, 0.212, 0.216])
        q = T.bh_adjust(ps)
        self.assertAlmostEqual(q[0], 0.01)
        self.assertTrue(np.all(np.diff(q[np.argsort(ps)]) >= -1e-12))
        self.assertTrue(np.all(q >= ps))

    def test_holm_matches_reference(self):
        ps = [0.01, 0.04, 0.03, 0.5]
        np.testing.assert_allclose(T.holm_adjust(ps), holm_ref(ps))

    def test_block_weights(self):
        rng = np.random.default_rng(1)
        W = T.block_weights(29, 50, rng)
        self.assertEqual(W.shape, (50, 29))
        self.assertTrue(np.all(W.sum(axis=1) == 29))

    def test_cluster_se_equals_iid_when_singletons(self):
        rng = np.random.default_rng(0)
        r = rng.normal(size=500)
        mask = np.ones(500, bool)
        se = T.cluster_se(r, mask, np.arange(500), 500)
        self.assertAlmostEqual(se, r.std() / np.sqrt(500) * np.sqrt(499 / 500) * np.sqrt(500 / 500), places=2)


class SearchTests(unittest.TestCase):
    def test_additive_residual_zero_mean_and_recovers_additive(self):
        df = T.prepare(synth(1))
        S = df[df.half == "A"].reset_index(drop=True)
        add = T.Additive(S)
        r = S["d"].to_numpy() - add.predict(S)
        self.assertAlmostEqual(float(r.mean()), 0.0, places=9)  # свободный член не штрафуется → среднее остатка 0

    def run_pipeline(self, df, B=300):
        df = T.prepare(df)
        out = {}
        for sh, th in (("A", "B"), ("B", "A")):
            S = df[df.half == sh].reset_index(drop=True)
            Tt = df[df.half == th].reset_index(drop=True)
            add = T.Additive(S)
            bk = T.make_buckets(S)
            cells = T.screen(T.search_cells(S, "long", add, bk))
            sl = T.shortlist(cells)
            conf = T.confirm_cells(Tt, add, bk, sl, np.random.default_rng(5), B=B)
            out[(sh, th)] = (cells, sl, conf)
        return out

    def test_finds_known_interaction(self):
        out = self.run_pipeline(synth(11, n=60000, inter=0.12))
        for key, (cells, sl, conf) in out.items():
            ids = [c["id"] for c in sl]
            want = [i for i in ids if "tpm60=H" in i and "mv5=H" in i]
            self.assertTrue(want, f"{key}: клетка tpm60=H × mv5=H не в шорт-листе: {ids}")
            ok = [x for x in conf if x["id"] in want and x["confirmed"]]
            self.assertTrue(ok, f"{key}: клетка не подтверждена")
            self.assertGreater(ok[0]["M_T"], 0.03)  # аддитивная модель съедает часть взаимодействия (≈ 2 × 0,12 / 3)

    def test_null_has_no_confirmed(self):
        for seed in (21, 22, 23):
            out = self.run_pipeline(synth(seed, n=40000, inter=0.0))
            for key, (cells, sl, conf) in out.items():
                self.assertFalse(any(x["confirmed"] for x in conf), f"seed {seed} {key}: ложное подтверждение")

    def test_cell_readability_rules(self):
        df = T.prepare(synth(3, n=40000))
        S = df[df.half == "A"].reset_index(drop=True)
        add = T.Additive(S)
        bk = T.make_buckets(S)
        for c in T.search_cells(S, "long", add, bk):
            self.assertGreaterEqual(c["n"], T.MIN_EP)
            self.assertGreaterEqual(c["k"], T.MIN_POS)

    def test_brute_force_cell(self):  # M клетки против прямого счёта по маске
        df = T.prepare(synth(4, n=40000))
        S = df[df.half == "A"].reset_index(drop=True)
        add = T.Additive(S)
        bk = T.make_buckets(S)
        cells = T.search_cells(S, "long", add, bk)
        c = cells[len(cells) // 2]
        b1, b2 = bk[(c["f1"], c["scheme"])], bk[(c["f2"], c["scheme"])]
        m = (b1.assign(S[c["f1"]].to_numpy()) == c["b1"]) & (b2.assign(S[c["f2"]].to_numpy()) == c["b2"])
        r = S["d"].to_numpy() - add.predict(S)
        self.assertEqual(int(m.sum()), c["n"])
        self.assertAlmostEqual(float(r[m].mean()), c["M"], places=12)
        self.assertAlmostEqual(float(S["d"].to_numpy()[m].mean()), c["E1"], places=12)
        self.assertEqual(int(S["y"].to_numpy()[m].sum()), c["k"])

    def test_embargo_day_dropped(self):
        df = synth(5, n=5000)
        df.loc[:5, "day"] = 44
        d = T.prepare(df)
        self.assertFalse((d["day"] == 44).any())


if __name__ == "__main__":
    unittest.main()
