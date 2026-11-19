"""Юнит-тесты П-42 §13.27 (TK-610): блоки и очистка с зоной августа, OOF не видит свой блок, слияние F с оценкой, ключи R0/R1/R2, пороги вердикта, охрана чтения и запечатанности,
NaN-оценка, склейка августа, ворота G2/G5, сквозная подгонка и оценка кандидатов на синтетике.
Запуск на srv: cd /data/p42-tk610/py && /data/tk592/venv/bin/python -m unittest test_tk610 (numpy, pandas, catboost, scipy)."""
import json
import os
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import retitr_cells as rc  # noqa: E402
import retitr_full as rf  # noqa: E402
import tk610_data as D  # noqa: E402
import tk610_fit as FIT  # noqa: E402
import tk610_gates as GT  # noqa: E402
import tk610_lib as T  # noqa: E402
import tk610_read as RD  # noqa: E402

DAY = T.DAY
BASE_L = "lat=4 latx=4 pk=1 pb=1 fh=4 fk=120 fm=50 fb=0.5 ew=5 ex=0 rf=0.2 rs=20"


class BlockTests(unittest.TestCase):
    def test_block_edges(self):
        d = np.array([-1, 0, 30, 31, 61, 62, 91, 92, 122, 123, 135, 136])
        self.assertEqual(T.block_of_abs(d).tolist(), [-1, 0, 0, -1, -1, 1, 1, 2, 2, 3, 3, -1])
        self.assertEqual(T.is_aug(d).tolist(), [False, False, False, True, True, False, False, False, False, False, False, False])

    def test_zone_hit(self):
        # зона [30·DAY; 63·DAY): сделка 31.07 (сутки 30) — внутри; сделка 29.07 с выходом в 31.07 — пересекает; сделка 02.09 (сутки 63) — вне
        self.assertTrue(T.zone_hit(30 * DAY + 5, 30 * DAY + 100))
        self.assertTrue(T.zone_hit(29 * DAY + 5, 30 * DAY + 1))
        self.assertFalse(T.zone_hit(29 * DAY + 5, 30 * DAY - 1))
        self.assertTrue(T.zone_hit(62 * DAY + 5, 62 * DAY + 100))
        self.assertFalse(T.zone_hit(63 * DAY, 63 * DAY + 5))

    def test_assert_search_days(self):
        T.assert_search_days(np.array([0, 30, 62, 135]))
        for bad in ([31], [61], [136], [-1], [5, 40]):
            with self.assertRaises(T.GuardError):
                T.assert_search_days(np.array(bad))


class PurgeTests(unittest.TestCase):
    def test_fold_train_excludes_zone_and_neighbours(self):
        # проверочный блок Б2 = сутки 62…91: обучение без соседних суток 61 и 92 и без пересечения с расширенным блоком; строки Б1 суток 30 (зона) тоже вне обучения
        aday = np.array([10, 29, 30, 62, 70, 91, 92, 93, 100, 130])
        a_entry = aday * DAY + 5
        a_exit = a_entry + 100
        tr = T.fold_train_idx(aday, a_entry, a_exit, 1)
        self.assertEqual(sorted(aday[tr].tolist()), [10, 29, 93, 100, 130])

    def test_overlap_into_block_removed(self):
        aday = np.array([60, 28])
        a_entry = np.array([60 * DAY, 28 * DAY])
        a_exit = np.array([62 * DAY + 10, 29 * DAY])    # первая сделка выходит в блок Б2
        tr = T.fold_train_idx(aday, a_entry, a_exit, 1)
        self.assertEqual(aday[tr].tolist(), [28])

    def test_val_rows_never_in_train(self):
        rng = np.random.default_rng(3)
        aday = np.concatenate([rng.integers(b0, b1 + 1, 400) for b0, b1 in T.BLOCKS])
        a_entry = aday * DAY + rng.integers(0, DAY, len(aday))
        a_exit = a_entry + rng.integers(0, 2 * DAY, len(aday))
        for k, (b0, b1) in enumerate(T.BLOCKS):
            tr = T.fold_train_idx(aday, a_entry, a_exit, k)
            self.assertFalse(((aday[tr] >= b0 - 1) & (aday[tr] <= b1 + 1)).any())
            self.assertFalse(T.zone_hit(a_entry[tr], a_exit[tr]).any())


class ScoreTests(unittest.TestCase):
    def test_oof_uses_own_block_model_and_aug_mean(self):
        blk = np.array([0, 1, 2, 3, -1])
        preds = {k: np.full(5, float(k)) for k in range(4)}
        s = T.pick_scores(blk, preds)
        self.assertEqual(s.tolist(), [0.0, 1.0, 2.0, 3.0, 1.5])     # август — среднее четырёх моделей

    def test_strict_requires_all_models(self):
        with self.assertRaises(T.GuardError):
            T.pick_scores(np.array([0, 2]), {0: np.zeros(2), 2: np.ones(2)})
        s = T.pick_scores(np.array([0, 1, 2]), {0: np.array([1.0, 1, 1]), 2: np.array([3.0, 3, 3])}, strict=False)   # сухой прогон: блок без модели — среднее
        self.assertEqual(s.tolist(), [1.0, 2.0, 3.0])

    def test_threshold_and_share(self):
        s = np.arange(1.0, 10.0)
        self.assertAlmostEqual(T.threshold_q(np.append(s, np.nan)), np.quantile(s, 2 / 3))
        self.assertAlmostEqual(T.selected_share(s, 7.0), 3 / 9)

    def test_f_pass_engine_rule(self):
        x = np.array([-5.0, 0.0, 2.0, np.nan])
        self.assertEqual(T.f_pass(x, "lo", 0.0).tolist(), [False, True, True, True])      # lo: отбрасывает value < fv; NaN проходит
        self.assertEqual(T.f_pass(x, "hi", 0.0).tolist(), [True, True, False, True])      # hi: отбрасывает value > fv

    def test_merge_with_filter(self):
        shat = np.array([0.1, 0.5, 0.9])
        out = T.merge_score(shat, np.array([True, False, True]))
        self.assertEqual(out.tolist(), [0.1, T.NEG, 0.9])

    def test_nan_score_is_refused(self):
        with self.assertRaises(T.GuardError):
            T.merge_score(np.array([0.1, np.nan]), np.array([True, True]))


class KeysTests(unittest.TestCase):
    def test_r0_r1_r2(self):
        c = BASE_L + " fa=maxmv1 fo=lo fv=0.5"
        self.assertEqual(T.strip_filter(c), BASE_L)
        r0 = rc.parse_keys(T.r0_keys(c))
        d0 = dict(r0[0])
        self.assertEqual(r0[1], [])
        self.assertEqual([d0[k] for k in ("natr_min", "t1h", "t24", "p", "c", "m", "s_glob", "ww", "busy")], ["0", "0", "0", "100000", "0", "1000000", "1000000", "0", "0"])
        self.assertEqual(T.r1_keys(c), c)
        r2 = rc.parse_keys(T.r2_keys(c, 0.123456789))
        self.assertEqual(r2[1], [{"fa": "score610", "fo": "lo", "fv": "0.123456789"}])
        self.assertEqual(dict(r2[0]), dict(rc.parse_keys(BASE_L)[0]))

    def test_g5_cells(self):
        p = rc.parse_keys(T.g5_keys(BASE_L, "pass"))[1]
        c = rc.parse_keys(T.g5_keys(BASE_L, "cut"))[1]
        self.assertEqual((p[0]["fv"], c[0]["fv"]), ("-1e18", "1e18"))

    def test_variants(self):
        cs = T.load_cstar({"L": BASE_L, "S": BASE_L + " tt=20"})
        rows = T.variant_rows(cs, {"L": 0.2})
        self.assertEqual([i for i, _ in rows["R0"]], ["L_r0", "S_r0"])
        self.assertEqual([i for i, _ in rows["R1"]], ["L_r1", "S_r1"])
        self.assertEqual([i for i, _ in rows["R2"]], ["L_r2", "L_g5pass", "L_g5cut"])

    def test_load_cstar_frozen_format(self):
        fz = {"version": "tk599-frozen-1", "sides": {"L": {"decision": "C*", "keys": BASE_L + " fa=maxmv1 fo=lo fv=0.5", "base_keys": BASE_L},
                                                      "S": {"decision": "база", "keys": BASE_L, "base_keys": BASE_L}}}
        c = T.load_cstar(fz)
        self.assertEqual(c["L"]["filter"], {"fa": "maxmv1", "fo": "lo", "fv": "0.5"})
        self.assertIsNone(c["S"]["filter"])
        with self.assertRaises(T.GuardError):
            T.load_cstar({"L": BASE_L + " fa=a fo=lo fv=1 fa=b fo=hi fv=2", "S": BASE_L})
        with self.assertRaises(T.GuardError):
            T.load_cstar({"L": BASE_L})


def _series(day, val, sym=None):
    day = np.asarray(day)
    sym = np.zeros(len(day), np.int64) if sym is None else np.asarray(sym)
    return rf.make_series(day, sym, val, None, None, np.zeros(len(day), np.int64), np.zeros(len(day), np.int64))


class VerdictTests(unittest.TestCase):
    def _pair(self, mean_b, n_b_per_day=6, seed=1, sigma=1.0):
        rng = np.random.default_rng(seed)
        days = np.repeat(np.arange(31, 62), 6)
        a = _series(days, rng.normal(0.0, sigma, len(days)), rng.integers(0, 40, len(days)))
        dayb = np.repeat(np.arange(31, 62), n_b_per_day)
        b = _series(dayb, rng.normal(mean_b, sigma, len(dayb)), rng.integers(0, 40, len(dayb)))
        return a, b

    def test_confirmed(self):
        a, b = self._pair(1.5)
        r = T.aug_compare(a, b, 40, nboot=2000)
        self.assertEqual(r["verdict"], "подтверждено")
        self.assertGreater(r["stat"].lower, 0)

    def test_refuted(self):
        a, b = self._pair(-1.5)
        r = T.aug_compare(a, b, 40, nboot=2000)
        self.assertEqual(r["verdict"], "опровергнуто")
        self.assertLess(r["upper"], 0)

    def test_unclear(self):
        a, b = self._pair(0.0, seed=5)
        self.assertEqual(T.aug_compare(a, b, 40, nboot=2000)["verdict"], "не ясно")

    def test_undefined_when_few_trades(self):
        a, b = self._pair(1.5)
        days = np.arange(31, 51)            # 20 сделок у B
        b = _series(days, np.ones(len(days)), np.zeros(len(days)))
        self.assertEqual(T.aug_compare(a, b, 40, nboot=500)["verdict"], "не определено")

    def test_sum_guard_blocks_confirmation(self):
        # Δ > 0, но ΣS_B < 0 при разности сумм сильно ниже −SE: подтверждения нет
        st = rf.DStat(d=0.5, se=0.1, z=5.0, lower=0.3, dsum=-50.0, se_sum=5.0, s_c=-10.0, s_r=40.0, n_c=40, n_r=200, n_days=31)
        self.assertEqual(T.aug_verdict(st, 0.7, True), "не ясно")
        st2 = rf.DStat(d=0.5, se=0.1, z=5.0, lower=0.3, dsum=-2.0, se_sum=5.0, s_c=10.0, s_r=12.0, n_c=40, n_r=200, n_days=31)
        self.assertEqual(T.aug_verdict(st2, 0.7, True), "подтверждено")
        self.assertEqual(T.aug_verdict(st2, 0.7, False), "не ясно")          # без устойчивости — нет
        st3 = rf.DStat(d=-0.5, se=0.1, z=-5.0, lower=-0.7, dsum=-2.0, se_sum=5.0, s_c=10.0, s_r=12.0, n_c=40, n_r=200, n_days=31)
        self.assertEqual(T.aug_verdict(st3, -0.3, True), "опровергнуто")

    def test_upper_matches_delta_stat_seed(self):
        rng = np.random.default_rng(7)
        Sc, Nc, Sr, Nr = rng.normal(1, 2, 31), rng.integers(1, 5, 31).astype(float), rng.normal(0, 2, 31), rng.integers(1, 5, 31).astype(float)
        st = rf.delta_stat(Sc, Nc, Sr, Nr, 3000)
        up = T.delta_upper(Sc, Nc, Sr, Nr, 3000)
        self.assertGreater(up, st.d)
        self.assertGreater(up, st.lower)

    def test_mde(self):
        lo = T.mde_pair(5.7, 43, 130, False)
        up = T.mde_pair(5.7, 43, 130, True)
        self.assertAlmostEqual(lo, 2.485 * 5.7 * np.sqrt(1 / 43 - 1 / 130), places=6)
        self.assertGreater(up, lo)
        self.assertTrue(np.isnan(T.mde_pair(5.7, 0, 130)))

    def test_describe_blocks_and_age(self):
        aday = np.array([1, 2, 3, 70, 71, 100])
        net = np.array([1.0, 2.0, 3.0, -1.0, 1.0, 5.0])
        sh = np.array([0.1, 0.9, 0.8, 0.2, 0.7, 0.6])
        r = T.describe_blocks(aday, net, sh, 0.5)
        self.assertEqual([x["n_sel"] for x in r], [2, 1, 1, 0])
        self.assertAlmostEqual(r[0]["S0"], 2.5)
        self.assertAlmostEqual(r[0]["S1"], 2.5 - 2.0)
        a = T.age_censor(np.array([10.0, 400.0, np.nan]))
        self.assertEqual((a["rows"], a["gt_days"]), (3, 1))


class SealTests(unittest.TestCase):
    def test_read_requires_frozen_sha_and_code(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "tk610-frozen.json")
            with self.assertRaises(T.GuardError):
                RD.verify_frozen(p, "x")                     # файла нет
            json.dump({"code_sha256": T.code_sha256()}, open(p, "w"))
            with self.assertRaises(T.GuardError):
                RD.verify_frozen(p, "0" * 64)                # sha256 не тот
            h = T.sha256_file(p)
            RD.verify_frozen(p, h)                           # всё сходится
            json.dump({"code_sha256": {"tk610_lib.py": "0" * 64}}, open(p, "w"))
            with self.assertRaises(T.GuardError):
                RD.verify_frozen(p, T.sha256_file(p))        # код изменён после заморозки

    def test_marker_once(self):
        with tempfile.TemporaryDirectory() as td:
            RD.take_marker(td)
            self.assertTrue(os.path.exists(os.path.join(td, "READ-aug.started")))
            with self.assertRaises(T.GuardError):
                RD.take_marker(td)

    def test_freeze_requires_green_gates(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(T.GuardError):
                RD.load_gates(td)
            for n in RD.GATES_REQUIRED:
                json.dump({"ok": True}, open(os.path.join(td, n + ".json"), "w"))
            RD.load_gates(td)
            json.dump({"ok": False}, open(os.path.join(td, "G5-wa-L.json"), "w"))
            with self.assertRaises(T.GuardError):
                RD.load_gates(td)


def _tab(rows):
    return rf.table_from_rows([dict(sym=s, entry_ms=e, exit_ms=x, gross=g, fees=f, funding=0.0, net=g - f, kind="stop") for s, e, x, g, f in rows])


class SpliceAugTests(unittest.TestCase):
    def test_splice_picks_august_only(self):
        wa, wb = rf.WINDOWS[0], rf.WINDOWS[1]
        # wa: T0 17.06 → сутки от 01.07 = entry//DAY − 14; wb: T0 25.07 → +24
        ta = _tab([("AAA", 44 * DAY + 5, 44 * DAY + 50, 1.0, 0.1),      # 31.07 — вне
                   ("AAA", 45 * DAY + 5, 45 * DAY + 50, 2.0, 0.1),      # 01.08 — внутри (сутки 31)
                   ("BBB", 69 * DAY + 5, 69 * DAY + 50, 3.0, 0.1),      # 25.08 — внутри (55)
                   ("BBB", 70 * DAY + 5, 70 * DAY + 50, 4.0, 0.1)])     # 26.08 — вне wa
        tb = _tab([("AAA", 31 * DAY + 5, 31 * DAY + 50, 5.0, 0.1),      # 25.08 — вне wb
                   ("AAA", 32 * DAY + 5, 32 * DAY + 50, 6.0, 0.1),      # 26.08 — внутри (56)
                   ("BBB", 37 * DAY + 5, 37 * DAY + 50, 7.0, 0.1),      # 31.08 — внутри (61)
                   ("BBB", 38 * DAY + 5, 38 * DAY + 50, 8.0, 0.1)])     # 01.09 — вне
        ser = T.splice_aug({"wa": ta, "wb": tb}, ["AAA", "BBB"])
        self.assertEqual(sorted(ser.day.tolist()), [31, 55, 56, 61])
        self.assertAlmostEqual(float(ser.val.sum()), 1.9 + 2.9 + 5.9 + 6.9, places=9)
        S, N = T.aug_slice(ser)
        self.assertEqual((len(S), int(N.sum())), (31, 4))

    def test_guards(self):
        ta = _tab([("AAA", 45 * DAY + 5, 45 * DAY + 50, 2.0, 0.1)])
        tb = _tab([("AAA", 32 * DAY + 5, 32 * DAY + 50, 6.0, 0.1)])
        with self.assertRaises(T.GuardError):
            T.splice_aug({"wa": ta, "wb": tb}, ["BBB"])                   # монета вне таблицы
        bad = rf.table_from_rows([dict(sym="AAA", entry_ms=45 * DAY + 5, exit_ms=45 * DAY + 50, gross=2.0, fees=0.1, funding=0.0, net=9.0, kind="stop")])
        with self.assertRaises(T.GuardError):
            T.splice_aug({"wa": bad, "wb": tb}, ["AAA"])                  # net ≠ gross − fees + funding
        da = {0: 0.0}
        with self.assertRaises(T.GuardError):
            T.splice_aug({"wa": ta, "wb": tb}, ["AAA"], {"wa": da, "wb": None})   # Σ daily среза ≠ Σ net


class GateTests(unittest.TestCase):
    def _mk(self, d, trades="x\n1\n", meta=None):
        os.makedirs(d)
        for n in GT.CSV4:
            open(os.path.join(d, "long-%s.csv" % n), "w").write(trades if n == "trades" else n + "\n")
        json.dump(meta or {"long": {"filter_[cands,dropped,no_key,no_value]": [10, 3, 0, 0]}}, open(os.path.join(d, "meta.json"), "w"))

    def test_compare_dirs_meta_ignores_id(self):
        with tempfile.TemporaryDirectory() as td:
            x, y = os.path.join(td, "x"), os.path.join(td, "y")
            self._mk(x, meta={"id": "L_r1", "long": {"n": 1}})
            self._mk(y, meta={"id": "c53a116125b39", "long": {"n": 1}})
            self.assertTrue(GT.compare_dirs(x, y, "long")["meta"]["equal"])
            json.dump({"id": "c53a116125b39", "long": {"n": 2}}, open(os.path.join(y, "meta.json"), "w"))
            self.assertFalse(GT.compare_dirs(x, y, "long")["meta"]["equal"])

    def test_g5(self):
        with tempfile.TemporaryDirectory() as td:
            r1, r2 = os.path.join(td, "r1"), os.path.join(td, "r2")
            self._mk(os.path.join(r1, "eng", "L_r1"))
            self._mk(os.path.join(r2, "eng", "L_g5pass"))
            self._mk(os.path.join(r2, "eng", "L_g5cut"), trades="x\n")
            self._mk(os.path.join(r2, "eng", "L_r2"))
            g = GT.gate_g5("wc", r2, r1, "L")
            self.assertTrue(g["ok"], g)
            self._mk(os.path.join(r2, "eng", "L_g5cut2"), trades="x\n1\n")
            os.rename(os.path.join(r2, "eng", "L_g5cut"), os.path.join(r2, "eng", "L_old"))
            os.rename(os.path.join(r2, "eng", "L_g5cut2"), os.path.join(r2, "eng", "L_g5cut"))
            self.assertFalse(GT.gate_g5("wc", r2, r1, "L")["ok"])           # «режет всех» оставил сделку

    def test_g5_r1_without_filter(self):
        # C* без фильтра: у R1 нет счётчиков фильтра — кандидатов сверяем со строками sigsx R1
        with tempfile.TemporaryDirectory() as td:
            r1, r2 = os.path.join(td, "r1"), os.path.join(td, "r2")
            self._mk(os.path.join(r1, "eng", "L_r1"), meta={"long": {"trades": 5}})
            open(os.path.join(r1, "eng", "L_r1", "long-sigsx.csv"), "w").write("h\n" + "x\n" * 10)
            self._mk(os.path.join(r2, "eng", "L_g5pass"))
            self._mk(os.path.join(r2, "eng", "L_g5cut"), trades="x\n")
            self._mk(os.path.join(r2, "eng", "L_r2"))
            self.assertTrue(GT.gate_g5("wc", r2, r1, "L")["cands_equal_r1"])
            open(os.path.join(r1, "eng", "L_r1", "long-sigsx.csv"), "w").write("h\n" + "x\n" * 9)
            self.assertFalse(GT.gate_g5("wc", r2, r1, "L")["cands_equal_r1"])

    def test_g5_no_key(self):
        with tempfile.TemporaryDirectory() as td:
            r1, r2 = os.path.join(td, "r1"), os.path.join(td, "r2")
            self._mk(os.path.join(r1, "eng", "L_r1"))
            self._mk(os.path.join(r2, "eng", "L_g5pass"))
            self._mk(os.path.join(r2, "eng", "L_g5cut"), trades="x\n")
            self._mk(os.path.join(r2, "eng", "L_r2"), meta={"long": {"filter_[cands,dropped,no_key,no_value]": [10, 3, 2, 0]}})
            self.assertFalse(GT.gate_g5("wc", r2, r1, "L")["no_key_zero"])


class F12RecordTests(unittest.TestCase):
    def _w(self, d, name, rec):
        json.dump({"rows": 1, **({"f12": rec} if rec is not None else {})}, open(os.path.join(d, name + ".info.json"), "w"))

    def test_f12_record(self):
        with tempfile.TemporaryDirectory() as td:
            rust = {"impl": "rust", "bin_md5": "ab", "days": 80, "store": "/s", "summary": "x"}
            self._w(td, "a", rust)
            self._w(td, "b", dict(rust, summary="y"))
            r = RD.f12_record([td])
            self.assertEqual((r["impl"], r["bin_md5"], r["tables"]), ("rust", "ab", 2))
            self._w(td, "c", {"impl": "python", "bin_md5": None})
            with self.assertRaises(T.GuardError):
                RD.f12_record([td])                      # смесь реализаций
            os.remove(os.path.join(td, "c.info.json"))
            self._w(td, "d", None)
            with self.assertRaises(T.GuardError):
                RD.f12_record([td])                      # старая таблица без записи f12


class MoreGateTests(unittest.TestCase):
    def test_g1(self):
        with tempfile.TemporaryDirectory() as td:
            sym = os.path.join(td, "symbols.txt")
            open(sym, "w").write("AAAUSDT,BBBUSDT\n")
            eng = os.path.join(td, "bin")
            open(eng, "wb").write(b"x")
            man = {"wa": ["manifest-trades 1 файлов, sha256 aa", "T0 2026-06-17 D0 2026-06-17 D1 2026-08-29"]}
            for w in ("wa", "wb", "wc"):
                os.makedirs(os.path.join(td, w))
                lines = man.get(w, ["manifest-trades 2 файлов, sha256 bb"])
                open(os.path.join(td, w, "MANIFEST"), "w").write("\n".join(lines) + "\n")
            mf = os.path.join(td, "m.txt")
            nl = chr(10)
            with open(mf, "w") as f:
                f.write(nl.join(["== wa"] + man["wa"] + ["== wb", "manifest-trades 2 файлов, sha256 bb", "== wc", "manifest-trades 2 файлов, sha256 bb"]) + nl)
                f.write(nl.join(["== symbols", "%s  /x/symbols.txt" % T.sha256_file(sym), "== engine", "binary abc md5 %s" % T.md5_file(eng)]) + nl)
            g = GT.gate_g1(td, mf, sym, eng)
            self.assertTrue(g["ok"], g)
            open(os.path.join(td, "wb", "MANIFEST"), "w").write("manifest-trades 3 файлов, sha256 cc\n")      # манифест окна изменился
            self.assertFalse(GT.gate_g1(td, mf, sym, eng)["windows"]["wb"]["equal"])
            open(eng, "wb").write(b"y")                                                                       # бинарь другой
            self.assertFalse(GT.gate_g1(td, mf, sym, eng)["engine_ok"])

    def test_g9(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "a.info.json")
            json.dump({"age_censor": T.age_censor(np.array([1.0, 2.0]))}, open(p, "w"))
            self.assertTrue(GT.gate_g9([p])["ok"])
            json.dump({"age_censor": T.age_censor(np.array([1.0, 400.0]))}, open(p, "w"))
            self.assertFalse(GT.gate_g9([p])["ok"])

    def test_g4_matches_reference_in_absolute_time(self):
        with tempfile.TemporaryDirectory() as td:
            rng = np.random.default_rng(9)
            n = 300
            ref_t0_off = 62 * DAY                                          # эталон с T0 01.09
            ref = pd.DataFrame({"sym": ["AAA"] * n, "sig_ms": np.arange(n) * 1000 + 5, "dist_e8": np.arange(n), "lp_e8": 7, "side": "long", "turn24h": rng.uniform(1e6, 1e8, n),
                                **{f: rng.normal(size=n) for f in T.FEATS if f != "lturn24"}})
            mine = ref.copy()
            mine["a_sig"] = mine["sig_ms"] + ref_t0_off
            mine["lturn24"] = np.log10(mine["turn24h"].clip(lower=1))
            mine["tick_ok"] = True
            rp, mp_ = os.path.join(td, "ref.parquet"), os.path.join(td, "mine.parquet")
            ref.to_parquet(rp)
            mine.to_parquet(mp_)
            g = GT.gate_g4(mp_, rp, "2026-09-01", "long")
            self.assertTrue(g["ok"], g)
            self.assertEqual(g["common_keys"], n)
            mine.loc[3, "zv6h"] += 1.0
            mine.to_parquet(mp_)
            self.assertFalse(GT.gate_g4(mp_, rp, "2026-09-01", "long")["ok"])
            self.assertFalse(GT.gate_g4(mp_, rp, "2026-09-01", "long", min_keys=1000)["ok"])

    def test_g3(self):
        with tempfile.TemporaryDirectory() as td:
            wc = rf.WINDOWS[2]
            for tag in ("R0", "R1"):
                d = os.path.join(td, "wc-%s" % tag, "eng", "L_%s" % tag.lower())
                os.makedirs(d)
                pd.DataFrame({"entry_ms": [(94 - 62) * DAY + 1, (100 - 62) * DAY]}).to_csv(os.path.join(d, "long-trades.csv"), index=False)
            g = GT.gate_g3(td, "L")
            self.assertTrue(g["ok"], g)
            self.assertEqual(g["runs"]["wc-R1"]["in_credit"], 2)
            pd.DataFrame({"entry_ms": [(136 - 62) * DAY + 1]}).to_csv(os.path.join(td, "wc-R1", "eng", "L_r1", "long-trades.csv"), index=False)   # сделка позже 13.11
            self.assertFalse(GT.gate_g3(td, "L")["ok"])


class DataTests(unittest.TestCase):
    def test_read_rows_drops_august_and_overlap(self):
        wc = rf.WINDOWS[2]                                   # T0 01.09 → 03.10 = сутки 94 от 01.07: credit 94…135
        off = T.win_offset_ms(wc)
        self.assertEqual(off, 62 * DAY)
        ent = [(94 - 62) * DAY + 5, (95 - 62) * DAY + 5, (80 - 62) * DAY + 5]   # сутки 94 (в диапазоне), 95, 80 (до зачётного начала wc — другое окно)
        with tempfile.TemporaryDirectory() as td:
            tr = pd.DataFrame({"sym": ["A", "B", "C"], "level": [1, 1, 1], "sig_ms": [e - 1000 for e in ent], "entry_ms": ent, "exit_ms": [e + 100 for e in ent],
                               "gross": [1.0, 2.0, 3.0], "fees": [0.1, 0.1, 0.1], "funding": 0.0, "net": [0.9, 1.9, 2.9], "kind": "stop"})
            p = os.path.join(td, "long-trades.csv")
            tr.to_csv(p, index=False)
            with open(os.path.join(td, "long-detail.jsonl"), "w") as f:
                for i, r in tr.iterrows():
                    f.write(json.dumps({"sym": r.sym, "level": int(r.level), "sig_ms": int(r.sig_ms), "legs": [[1, 2, 500_000_000, True]]}) + "\n")
            keys, n_all, n_keep = D.read_keys_dates(p, wc)
            self.assertEqual((n_all, n_keep), (3, 2))
            rows = D.read_rows(p, os.path.join(td, "long-detail.jsonl"), wc, keys)
            self.assertEqual(sorted(rows["sym"]), ["A", "B"])
            self.assertEqual(rows["nominal"].tolist(), [500.0, 500.0])

    def test_search_keep_windows(self):
        wa, wb = rf.WINDOWS[0], rf.WINDOWS[1]
        # `wa`: зачёт — сутки 0…55; августовские сутки 31…55 — не строки обучения; `wb`: зачёт 56…93, август 56…61 вне, Б2 с 62
        self.assertEqual(D.search_keep(np.array([0, 29, 30, 40, 55, 56, 62]) * DAY + 5, wa).tolist(), [True, True, True, False, False, False, False])
        self.assertEqual(D.search_keep(np.array([55, 56, 61, 62, 91, 92, 93, 94]) * DAY + 5, wb).tolist(), [False, False, False, True, True, True, True, False])

    def test_trades_per_day(self):
        wc = rf.WINDOWS[2]
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "t.csv")
            pd.DataFrame({"entry_ms": [(94 - 62) * DAY + 1, (94 - 62) * DAY + 9, (95 - 62) * DAY], "gross": [9.0, 9.0, 9.0]}).to_csv(p, index=False)
            c = D.trades_per_day(p, wc)
            self.assertEqual((int(c[94]), int(c[95]), int(c.sum())), (2, 1, 3))


def _synth_train(n=3400, seed=2):
    rng = np.random.default_rng(seed)
    parts = []
    for k, (b0, b1) in enumerate(T.BLOCKS):
        m = n // 4
        day = rng.integers(b0, b1 + 1, m)
        ent = day * DAY + rng.integers(0, DAY // 2, m)
        X = rng.normal(size=(m, len(T.FEATS)))
        parts.append(pd.DataFrame({"sym": rng.choice(["AAA", "BBB", "CCC", "DDD"], m), "level": rng.integers(1, 5, m), "a_sig": ent - 1000 + np.arange(m) * 7 + k * 100_000,
                                   "aday": day, "a_entry": ent, "a_exit": ent + rng.integers(1000, 600_000, m), "net_nf": 0.8 * X[:, 0] + rng.normal(0, 1, m),
                                   **{f: X[:, i] for i, f in enumerate(T.FEATS)}}))
    return pd.concat(parts, ignore_index=True)


def _synth_cand(win, seed=4):
    w = {x.name: x for x in rf.WINDOWS}[win]
    lo, hi = T.credit_days(w)
    rng = np.random.default_rng(seed)
    m = 600
    day = rng.integers(lo, hi + 1, m)
    a_sig = day * DAY + rng.integers(0, DAY, m)
    X = rng.normal(size=(m, len(T.FEATS)))
    return pd.DataFrame({"sym": rng.choice(["AAA", "BBB", "CCC"], m), "level": 1, "sig_ms": a_sig - T.win_offset_ms(w) + np.arange(m), "tick_ok": True,
                         **{f: X[:, i] for i, f in enumerate(T.FEATS)}, "a_sig": a_sig + np.arange(m), "aday": day, "win": win})


class EndToEndTests(unittest.TestCase):
    def test_fit_and_score_on_synthetic(self):
        old = T.ITER_CAP
        T.ITER_CAP = 40
        try:
            with tempfile.TemporaryDirectory() as td:
                U = _synth_train()
                tp = os.path.join(td, "train.parquet")
                U.to_parquet(tp)
                fit = FIT.fit_side("L", [tp], os.path.join(td, "fit"), 1)
                self.assertNotIn("undefined", fit)
                self.assertEqual([f["block"] for f in fit["folds"]], [0, 1, 2, 3])
                self.assertEqual(len(fit["cv"]), 12)
                self.assertGreaterEqual(fit["iterations"], 10)
                # фолд k не обучался на строках блока k (очистка): обучающих меньше всех строк и без зоны
                self.assertTrue(all(n < len(U) for n in fit["train_rows_per_fold"].values()))
                paths = []
                for w in ("wa", "wb", "wc"):
                    p = os.path.join(td, "cand-%s.parquet" % w)
                    _synth_cand(w).to_parquet(p)
                    paths.append(p)
                res = FIT.score_side("L", os.path.join(td, "fit"), paths, os.path.join(td, "score"))
                self.assertEqual(res["aug_cands"], sum(1 for p in paths for d in pd.read_parquet(p)["aday"] if 31 <= d <= 61))
                self.assertGreater(res["n_oof"], 0)
                for w in ("wa", "wb", "wc"):
                    n = len(pd.read_parquet(os.path.join(td, "cand-%s.parquet" % w)))
                    syms, ms, cols = D.rfx.read_ftab(os.path.join(td, "score", "ftab", w, "L-score610.csv"))
                    self.assertEqual(len(syms), n)
                    self.assertFalse(np.isnan(cols[T.SCORE_COL]).any())
                sh = pd.read_parquet(os.path.join(td, "score", "shat-L.parquet"))
                self.assertFalse(sh["shat"].isna().any())
                # OOF: кандидат блока k оценён моделью фолда k, остальные блоки — не ею; август — среднее
                fit2, models = FIT.load_models(os.path.join(td, "fit"), "L")
                c = pd.read_parquet(paths[2])
                m = FIT.xmat(c)
                k = 3
                blk = T.block_of_abs(c["aday"].to_numpy())
                want = models[k].predict(m)[blk == k]
                got = sh[(sh["win"] == "wc")].sort_values("sig_ms").reset_index(drop=True)
                ref = c.assign(blk=blk).sort_values("sig_ms").reset_index(drop=True)
                self.assertTrue(np.allclose(np.sort(got["shat"].to_numpy()[ref["blk"].to_numpy() == k]), np.sort(want)))
        finally:
            T.ITER_CAP = old

    def test_fit_refuses_august_rows(self):
        with tempfile.TemporaryDirectory() as td:
            U = _synth_train()
            U.loc[0, "aday"] = 40
            tp = os.path.join(td, "train.parquet")
            U.to_parquet(tp)
            with self.assertRaises(T.GuardError):
                FIT.load_train([tp])

    def test_undefined_when_few_rows(self):
        with tempfile.TemporaryDirectory() as td:
            U = _synth_train(400)
            tp = os.path.join(td, "train.parquet")
            U.to_parquet(tp)
            res = FIT.fit_side("S", [tp], os.path.join(td, "fit"), 1)
            self.assertIn("undefined", res)


if __name__ == "__main__":
    unittest.main()
