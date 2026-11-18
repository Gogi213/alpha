"""Юнит-тесты П-42 §13.25 п. 6 (TK-600): заморозка X-руки (`tk600_freeze.py`) и чтение окон (`tk600_read.py`) — вердикты на синтетике, хэши, маркер одного чтения, сквозной прогон на тестовом окне F.
Запуск на srv: cd /data/p42-tk600/py && /data/tk592/venv/bin/python -m unittest test_tk600_read (numpy, pandas, catboost, scipy)."""
import argparse
import json
import os
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk598_lib as L  # noqa: E402
import tk600_feat as F6  # noqa: E402
import tk600_freeze as FZ  # noqa: E402
import tk600_read as RD  # noqa: E402

DAY = L.DAY


def synth_pool(per_day=4, effect=(10.0, 10.0), x_sign=(1.0, 1.0), seed=1):
    """Строки двух окон (62 суток): net = effect·z + шум; ŝ руки X = x_sign·z + малый шум, ŝ руки f12 — чистый шум. effect / x_sign — по месяцам (июль, август)."""
    rng = np.random.default_rng(seed)
    parts = []
    for i, w in enumerate(RD.POOL_WINS):
        n = 31 * per_day
        z = rng.normal(size=n)
        df = pd.DataFrame({"sym": [f"C{j % 12}" for j in range(n)], "win": w, "pday": np.repeat(np.arange(31) + 31 * i, per_day), "day": np.repeat(np.arange(31) + 14, per_day),
                           "net_nf": effect[i] * z + rng.normal(0, 5, n), "sX_T1": x_sign[i] * z + rng.normal(0, 0.2, n), "sF_T1": rng.normal(size=n)})
        parts.append(df)
    df = pd.concat(parts, ignore_index=True)
    qx = float(np.quantile(df["sX_T1"], 2 / 3))
    qf = float(np.quantile(df["sF_T1"], 2 / 3))
    fs = {"targets": ["T1"], "arms": {"X": {"T1": {"q": qx, "lower": False}}, "f12": {"T1": {"q": qf, "lower": False}}}}
    return df, fs


class VerdictTests(unittest.TestCase):
    idx = L.boot_indices(62, b=2000, block=4, seed=600)

    def run_v(self, **kw):
        df, fs = synth_pool(**kw)
        return RD.verdicts_pair(df, fs, ["T1"], self.idx)["T1"]

    def test_confirmed_for_strong_effect(self):
        r = self.run_v()
        self.assertGreaterEqual(min(r["n_sel_X"], r["n_sel_f12"]), L.MIN_SEL)
        self.assertGreater(r["D"], 5.0)
        self.assertTrue(r["holm_reject"] and r["D_jul_pos"] and r["D_aug_pos"])
        self.assertEqual(r["verdict"], "подтверждено")

    def test_not_confirmed_for_noise(self):
        r = self.run_v(effect=(0.0, 0.0), seed=7)
        self.assertNotEqual(r["verdict"], "подтверждено")

    def test_refuted_for_negative_effect(self):
        r = self.run_v(x_sign=(-1.0, -1.0), seed=3)
        self.assertEqual(r["verdict"], "опровергнуто")

    def test_not_confirmed_when_one_month_negative(self):
        r = self.run_v(effect=(14.0, 6.0), x_sign=(1.0, -1.0), seed=5)
        self.assertFalse(r["D_aug_pos"])
        self.assertNotEqual(r["verdict"], "подтверждено")

    def test_undefined_when_few_selected(self):
        r = self.run_v(per_day=1)
        self.assertLess(r["n_sel_X"], L.MIN_SEL)
        self.assertTrue(r["verdict"].startswith("не определена"))

    def test_same_bootstrap_indices_and_holm_over_targets(self):
        df, fs = synth_pool()
        df["sX_T3"], df["sF_T3"] = df["sX_T1"], df["sF_T1"]
        fs["targets"] = ["T1", "T3"]
        fs["arms"]["X"]["T3"], fs["arms"]["f12"]["T3"] = fs["arms"]["X"]["T1"], fs["arms"]["f12"]["T1"]
        out = RD.verdicts_pair(df, fs, ["T1", "T3"], self.idx)
        self.assertEqual(out["T1"]["m"], 2)
        self.assertEqual(out["T1"]["p_D"], out["T3"]["p_D"])
        self.assertEqual(out["T1"]["D"], out["T3"]["D"])

    def test_pooled_day_index(self):
        with tempfile.TemporaryDirectory() as td:
            for w, d in (("jul", 14), ("aug", 14)):
                pd.DataFrame({"sym": ["A"], "level": [1], "sig_ms": [d * DAY], "day": [d], "net_nf": [1.0]}).to_parquet(os.path.join(td, f"rows-{w}-long.parquet"))
            pd.DataFrame({"sym": ["A"], "level": [1], "sig_ms": [44 * DAY], "day": [44], "net_nf": [1.0]}).to_parquet(os.path.join(td, "rows-aug-long.parquet"))
            self.assertEqual(RD.pooled_rows(td, "long")["pday"].tolist(), [0, 61])


class GuardTests(unittest.TestCase):
    def frozen(self, td, tamper=None, inputs=None):
        here = os.path.dirname(os.path.abspath(RD.__file__))
        code = {fn: RD.sha256(os.path.join(here, fn)) for fn in sorted(os.listdir(here)) if (fn.startswith(("tk600_", "tk598_")) and fn.endswith(".py")) or fn == "tk594_feat.py"}
        if tamper:
            code[tamper] = "0" * 64
        p = os.path.join(td, "frozen600.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"code_sha256": code, "sides": {}, "inputs_sha256": inputs or {}}, f)
        return p

    def test_hash_checked_code(self):
        with tempfile.TemporaryDirectory() as td:
            p = self.frozen(td)
            self.assertIn("tk600_read.py", json.load(open(p))["code_sha256"])
            self.assertIn("tk594_feat.py", json.load(open(p))["code_sha256"])
            self.assertIn("code_sha256", RD.verify(p, RD.sha256(p)))
            p = self.frozen(td, tamper="tk600_read.py")
            with self.assertRaises(SystemExit):
                RD.verify(p, RD.sha256(p))
            with self.assertRaises(SystemExit):
                RD.verify(p, "0" * 64)
            with self.assertRaises(SystemExit):
                RD.verify(os.path.join(td, "нет.json"), "0" * 64)

    def test_input_hash(self):
        with tempfile.TemporaryDirectory() as td:
            f = os.path.join(td, "feat-X-jul-1-L_u1.parquet")
            pd.DataFrame({"a": [1]}).to_parquet(f)
            fr = {"inputs_sha256": {"feat-X-jul-1-L_u1.parquet": RD.sha256(f)}}
            RD.check_inputs(fr, td)
            with open(f, "ab") as h:
                h.write(b"x")
            with self.assertRaises(SystemExit):
                RD.check_inputs(fr, td)

    def test_marker_once(self):
        with tempfile.TemporaryDirectory() as td:
            m = RD.claim_marker(td, "jul")
            self.assertTrue(m.endswith("READ600-jul.started"))
            with self.assertRaises(SystemExit):
                RD.claim_marker(td, "jul")
            RD.claim_marker(td, "aug")
            self.assertTrue(RD.claim_marker(td, "F").endswith("READ600-F-TEST.started"))

    def test_requires_tk598_markers(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(SystemExit):
                RD.check_t598_read(td)
            with self.assertRaises(SystemExit):
                RD.check_t598_read("")
            open(os.path.join(td, "READ-jul.started"), "w").close()
            with self.assertRaises(SystemExit):
                RD.check_t598_read(td)
            open(os.path.join(td, "READ-aug.started"), "w").close()
            RD.check_t598_read(td)


def train_models(dst, side, sub, nfeat, seed):
    from catboost import CatBoostRegressor
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(300, nfeat))
    y = 3 * X[:, 0] + rng.normal(size=300)
    for k in range(5):
        m = CatBoostRegressor(iterations=8, depth=2, learning_rate=0.3, random_seed=seed + k, verbose=0, allow_writing_files=False, thread_count=1)
        m.fit(X, y)
        m.save_model(os.path.join(dst, f"{side}_{sub}_f{k}.cbm"))


class FreezeReadTests(unittest.TestCase):
    def setup_freeze(self, td):
        src_x, src_f, oof = (os.path.join(td, d) for d in ("fitX", "fit12", "oof"))
        for d in (src_x, src_f, oof):
            os.makedirs(d)
        train_models(src_x, "long", "net", len(F6.X_FEATS), 10)
        train_models(src_f, "long", "net", len(F6.F12), 20)
        rng = np.random.default_rng(0)
        for arm in ("X", "f12"):
            pd.DataFrame({"sym": ["A"] * 50, "level": np.arange(50), "sig_ms": np.arange(50), "day": 14, "s_T1": rng.normal(size=50), "s_T3": rng.normal(size=50)}).to_parquet(
                os.path.join(oof, f"oof-{arm}-long-u1.parquet"))
        st = {"sides": {"long": {"targets": [{"target": "T1", "admit_stage2": True}, {"target": "T3", "admit_stage2": False}]},
                        "short": {"targets": [{"target": "T1", "admit_stage2": False}]}}}
        sp = os.path.join(td, "stage1.json")
        json.dump(st, open(sp, "w"))
        out = os.path.join(td, "freeze")
        fr = FZ.freeze(argparse.Namespace(stage1=sp, models_x=src_x, models_f12=src_f, oof_dir=oof, feat_dir=td, out=out))
        return fr, out, oof

    def test_freeze_admission_thresholds_and_hashes(self):
        with tempfile.TemporaryDirectory() as td:
            fr, out, oof = self.setup_freeze(td)
            self.assertEqual(list(fr["sides"]), ["long"])
            self.assertEqual(fr["sides"]["long"]["targets"], ["T1"])
            s = pd.read_parquet(os.path.join(oof, "oof-X-long-u1.parquet"))["s_T1"].to_numpy()
            self.assertEqual(fr["sides"]["long"]["arms"]["X"]["T1"]["q"], float(np.quantile(s, 2 / 3)))
            self.assertFalse(fr["sides"]["long"]["arms"]["X"]["T1"]["lower"])
            m = RD.load_models(fr, {"X": os.path.join(out, "models-X"), "f12": os.path.join(out, "models-f12")})
            self.assertEqual(set(m), {("X", "long", "net"), ("f12", "long", "net")})
            with open(os.path.join(out, "models-X", "long_net_f0.cbm"), "ab") as f:
                f.write(b"x")
            with self.assertRaises(SystemExit):
                RD.load_models(fr, {"X": os.path.join(out, "models-X"), "f12": os.path.join(out, "models-f12")})

    def test_freeze_none_admitted_writes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            sp = os.path.join(td, "stage1.json")
            json.dump({"sides": {"long": {"targets": [{"target": "T1", "admit_stage2": False}]}}}, open(sp, "w"))
            fr = FZ.freeze(argparse.Namespace(stage1=sp, models_x=td, models_f12=td, oof_dir=td, feat_dir=td, out=os.path.join(td, "o")))
            self.assertIsNone(fr)
            self.assertFalse(os.path.exists(os.path.join(td, "o", "frozen600.json")))

    def test_read_window_on_test_window_f(self):
        with tempfile.TemporaryDirectory() as td:
            fr, out, _ = self.setup_freeze(td)
            fp = os.path.join(out, "frozen600.json")
            n = 60
            rng = np.random.default_rng(4)
            day = 14 + (np.arange(n) % 60)
            sig = day * DAY + 5 + np.arange(n)
            net = rng.normal(0, 4, n)
            syms = [f"C{i % 7}" for i in range(n)]
            run = os.path.join(td, "run", "F-1")
            eng = os.path.join(run, "eng", "L_u1")
            os.makedirs(eng)
            pd.DataFrame({"sym": syms, "level": np.arange(n), "sig_min": 1, "entry_min": 1, "exit_min": 2, "entry_px_e8": 1, "kind": np.where(np.arange(n) % 5 == 0, "stop", "trail"),
                          "takes": 0, "gross": net + 0.3, "fees": 0.3, "funding": 0.0, "net": net, "entry_day": 0, "sig_ms": sig, "entry_ms": sig + 4, "exit_ms": sig + 99}
                         ).to_csv(os.path.join(eng, "long-trades.csv"), index=False)
            with open(os.path.join(eng, "long-detail.jsonl"), "w") as f:
                for i in range(n):
                    f.write(json.dumps({"sym": syms[i], "level": i, "sig_ms": int(sig[i]), "natr_e8": 400000, "legs": [[1, 1, 500_000_000, False]]}) + "\n")
            pd.DataFrame({"day": day, "pnl": net}).to_csv(os.path.join(eng, "long-daily.csv"), index=False)
            feat = pd.DataFrame({"sym": syms, "level": np.arange(n), "sig_ms": sig, "tick_ok": True})
            Xv = rng.normal(size=(n, len(F6.X_FEATS)))
            for i, c in enumerate(F6.X_FEATS):
                feat[c] = Xv[:, i]
            fd = os.path.join(td, "feat")
            os.makedirs(fd)
            feat.to_parquet(os.path.join(fd, "feat-X-F-1-L_u1.parquet"))
            a = argparse.Namespace(win="F", frozen=fp, frozen_sha256=RD.sha256(fp), models_x=os.path.join(out, "models-X"), models_f12=os.path.join(out, "models-f12"), run=run, feat_dir=fd,
                                   out=os.path.join(td, "read"), t598_read_dir="")
            RD.read_window(a)
            rows = pd.read_parquet(os.path.join(td, "read", "rows-F-TEST-long.parquet"))
            self.assertEqual(len(rows), n)
            m = RD.load_models(fr, {"X": a.models_x, "f12": a.models_f12})
            kind, ms = m[("X", "long", "net")]
            want = np.mean([x.predict(feat[F6.X_FEATS].to_numpy(np.float64)) for x in ms], axis=0)
            self.assertTrue(np.allclose(rows["sX_T1"].to_numpy(), want))
            kind, ms = m[("f12", "long", "net")]
            want = np.mean([x.predict(feat[F6.F12].to_numpy(np.float64)) for x in ms], axis=0)
            self.assertTrue(np.allclose(rows["sF_T1"].to_numpy(), want))
            res = json.load(open(os.path.join(td, "read", "read-F-TEST.json")))
            self.assertTrue(res["gw_ok"])
            self.assertIn("skipped", res["sides"]["short"])
            with self.assertRaises(SystemExit):                                    # повтор отказывает
                RD.read_window(a)
            a2 = argparse.Namespace(**dict(vars(a), win="jul", out=os.path.join(td, "read2")))
            with self.assertRaises(SystemExit):                                    # без маркеров TK-598 окно не читается
                RD.read_window(a2)
            self.assertFalse(os.path.exists(os.path.join(td, "read2", "READ600-jul.started")))


if __name__ == "__main__":
    unittest.main()
