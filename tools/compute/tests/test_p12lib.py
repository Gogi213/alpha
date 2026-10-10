"""TK-135: p12lib.load_head — голова исполняется до маркера, argv подменяется на время и возвращается; sr/block_idx — эталонные значения."""
import os, sys
import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import p12lib


def test_load_head_imports_module_and_restores_argv(tmp_path):
    f = tmp_path / "head.py"
    f.write_text("import sys\nMODE = sys.argv[1]\nMARK = 1\nTAIL = 2\n", encoding="utf-8")
    argv0 = list(sys.argv)
    ns = p12lib.load_head(str(f), ["B2"])
    assert ns["MODE"] == "B2" and ns["TAIL"] == 2
    assert sys.argv == argv0


def test_load_head_restores_argv_on_error(tmp_path):
    f = tmp_path / "bad.py"
    f.write_text("raise RuntimeError('x')\nMARK = 1\n", encoding="utf-8")
    argv0 = list(sys.argv)
    with pytest.raises(RuntimeError):
        p12lib.load_head(str(f), ["free"])
    assert sys.argv == argv0


def test_heads_exist():
    d = os.path.join(os.path.dirname(p12lib.__file__))
    for h in p12lib.HEADS.values():
        assert os.path.exists(os.path.join(d, h))
        assert "exec(compile" not in open(os.path.join(d, h), encoding="utf-8").read()


def test_sr_known_values():
    x = np.array([1.0, 2.0, 3.0, 4.0])
    assert p12lib.sr(x, np.ones(4)) == pytest.approx(2.5 / np.std(x, ddof=1))
    assert p12lib.sr(np.ones(4), np.ones(4)) == 0.0          # sd = 0 -> 0
    assert p12lib.sr(x, np.array([1.0, 1.0, 0.0, 0.0])) == pytest.approx(1.5 / np.std(x[:2], ddof=1))


def test_block_idx_shape_and_range():
    idx = p12lib.block_idx(np.random.default_rng(1), 30)
    assert idx.shape == (p12lib.B, 30) and idx.min() >= 0 and idx.max() < 30


def test_trials_effn_counts_and_correlation():
    rng = np.random.default_rng(7)
    base = rng.normal(size=60)
    days = {"B1": dict(enumerate(base)), "copy": dict(enumerate(base * 2)), "flat": {}, "indep": dict(enumerate(rng.normal(size=60)))}
    e = p12lib.trials_effn(days, 0, 59, "B1")
    assert e["trials"] == 4 and e["flat"] == 1 and e["n_days"] == 60
    assert 1.0 < e["n_eff_cells"] < 3.0          # B1 и copy — один ряд, indep — второй: ≈ 2
    assert e["n_eff_ref"] is None or 1.0 <= e["n_eff_ref"] <= 60


def test_p12args_flag_sets_env_default_keeps_env(monkeypatch):
    import p12args
    monkeypatch.setenv("P12_OUT", "из-окружения"); monkeypatch.delenv("P12_DIR", raising=False)
    monkeypatch.setattr(sys, "argv", ["x", "B2", "--dir", "data/x/"])
    p12args.apply()
    assert os.environ["P12_DIR"] == "data/x/" and os.environ["P12_OUT"] == "из-окружения"   # флаг ставит, незаданное — прежнее окружение
    assert sys.argv == ["x", "B2"]                                                           # флаги вырезаны, позиционный режим цел
