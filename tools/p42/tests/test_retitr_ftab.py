"""Тесты таблицы признаков кандидатов F (retitr_ftab.py): формат CSV (NaN — пусто, ключи), границы срезов (квантили по A, направления,
ничьи), сравнение с таблицами TK-594, строки ворот движка и стыковка с retitr_cells.py. Без pytest и без pandas:
`PYTHONUTF8=1 python3 tools/p42/tests/test_retitr_ftab.py` — выполняет все test_* и печатает итог.
"""
import json
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import retitr_cells as rc  # noqa: E402
import retitr_ftab as rf  # noqa: E402

DAY = rf.DAY_MS


def tmp(name):
    return os.path.join(tempfile.mkdtemp(), name)


# ---- формат таблицы ----------------------------------------------------------------------------------------------------

def test_csv_format_nan_empty_and_roundtrip():
    p = tmp("t.csv")
    syms = ["BTCUSDT", "ETHUSDT", "AAVEUSDT"]
    ms = np.array([1_209_841_228, 5, 7_000_000_000], dtype=np.int64)
    a = np.array([0.1, float("nan"), -3.5])
    b = np.array([1.0 / 3.0, 2.0, 1e-12])
    rf.write_ftab(p, syms, ms, ["f1", "f2"], [a, b])
    text = open(p, encoding="utf-8").read().splitlines()
    assert text[0] == "sym,sig_ms,f1,f2"
    assert text[2] == "ETHUSDT,5,,2.0", text[2]            # NaN — пустая ячейка (так читает движок: parse_val пусто → NaN)
    s2, m2, c2 = rf.read_ftab(p)
    assert s2 == syms and list(m2) == list(ms)
    assert np.isnan(c2["f1"][1]) and c2["f1"][0] == 0.1 and c2["f2"][0] == 1.0 / 3.0 and c2["f2"][2] == 1e-12   # без потерь (repr float)


def test_csv_key_must_be_unique():
    p = tmp("d.csv")
    try:
        rf.write_ftab(p, ["A", "A"], np.array([1, 1]), ["f"], [np.array([1.0, 2.0])])
    except SystemExit:
        return
    raise AssertionError("дубль ключа (sym, sig_ms) должен отвергаться")


def test_read_ftab_accepts_engine_missing_tokens():
    p = tmp("m.csv")
    open(p, "w", encoding="utf-8", newline="\n").write("sym,sig_ms,f\nA,1,nan\nA,2,NA\nA,3,None\nA,4,\nA,5,2.5\n")
    _, _, c = rf.read_ftab(p)
    assert list(np.isnan(c["f"])) == [True, True, True, True, False] and c["f"][4] == 2.5


def test_read_ftab_header_check():
    p = tmp("h.csv")
    open(p, "w", encoding="utf-8").write("coin,sig_ms,f\nA,1,1\n")
    try:
        rf.read_ftab(p)
    except ValueError:
        return
    raise AssertionError("заголовок должен начинаться с `sym,sig_ms`")


# ---- границы срезов ----------------------------------------------------------------------------------------------------

def test_a_mask_boundaries():
    ms = np.array([13 * DAY + DAY - 1, 14 * DAY, 43 * DAY + DAY - 1, 44 * DAY, 0], dtype=np.int64)
    assert list(rf.a_mask(ms)) == [False, True, True, False, False]   # сутки 14…43 включительно, граница по целым суткам


def test_quantile_cut_directions_and_nan():
    x = np.arange(1.0, 101.0)               # 1…100
    x = np.concatenate([x, [float("nan")] * 50])   # NaN не участвуют в квантиле
    assert rf.quantile_cut(x, "lo", 10) == np.quantile(np.arange(1.0, 101.0), 0.10)
    assert rf.quantile_cut(x, "hi", 10) == np.quantile(np.arange(1.0, 101.0), 0.90)
    assert rf.quantile_cut(x, "hi", 25) == np.quantile(np.arange(1.0, 101.0), 0.75)
    assert abs(rf.quantile_cut(x, "lo", 25) - 25.75) < 1e-12    # линейная квантиль numpy: 1 + 0,25·99
    # доля реально отбрасываемого — как правило движка (lo: x < fv; hi: x > fv)
    fv = rf.quantile_cut(x, "lo", 20)
    assert abs(rf.dropped_share(x, "lo", fv) - 0.20) < 0.011
    fv = rf.quantile_cut(x, "hi", 20)
    assert abs(rf.dropped_share(x, "hi", fv) - 0.20) < 0.011


def test_quantile_cut_ties_drop_less_than_p():
    x = np.array([0.0] * 80 + [1.0] * 20)   # дискретный признак: q(0,25) = 0 → x < 0 не отбрасывает ничего
    fv = rf.quantile_cut(x, "lo", 25)
    assert fv == 0.0 and rf.dropped_share(x, "lo", fv) == 0.0
    fv = rf.quantile_cut(x, "hi", 10)       # q(0,9) = 1 → x > 1 не отбрасывает ничего
    assert fv == 1.0 and rf.dropped_share(x, "hi", fv) == 0.0


def test_quantile_cut_empty_raises():
    try:
        rf.quantile_cut(np.array([float("nan")]), "lo", 10)
    except ValueError:
        return
    raise AssertionError


def test_build_feat_json_only_A_and_format_feeds_cells():
    rng = np.random.default_rng(7)
    n = 600
    day = rng.integers(0, 74, n)
    ms = day * DAY + np.arange(n)           # ключи уникальны
    cols = {nm: rng.normal(size=n) for nm, _ in rf.FEATURES["S"]}
    cols["tpm15"] = np.where(day < 14, 1e6, cols["tpm15"])      # значения вне A не должны влиять на границы
    cols["tpm15"][::9] = np.nan
    p = tmp("s.csv")
    rf.write_ftab(p, ["C%d" % i for i in range(n)], ms, [nm for nm, _ in rf.FEATURES["S"]], [cols[nm] for nm, _ in rf.FEATURES["S"]])
    feats, diag = rf.build_feat_json("S", p)
    assert [f["name"] for f in feats] == ["tpm15", "cons60", "tpm60", "big1800", "since_born_h"]
    assert [f["fo"] for f in feats] == ["lo", "hi", "lo", "lo", "lo"]
    am = rf.a_mask(ms)
    xa = cols["tpm15"][am]
    xa = xa[~np.isnan(xa)]
    assert feats[0]["cuts"]["10"] == float(np.quantile(xa, 0.10)) and feats[0]["cuts"]["25"] == float(np.quantile(xa, 0.25))
    assert diag[0]["n_cand_A"] == int(am.sum()) and diag[0]["n_nan_A"] == int(np.isnan(cols["tpm15"][am]).sum())
    assert feats[1]["cuts"]["10"] == float(np.quantile(cols["cons60"][am], 0.90))   # hi: q(1 − p)
    assert list(feats[0]["cuts"]) == ["10", "20", "25"] and feats[0]["cuts"]["10"] < feats[0]["cuts"]["20"] < feats[0]["cuts"]["25"]
    assert feats[1]["cuts"]["10"] > feats[1]["cuts"]["20"] > feats[1]["cuts"]["25"]      # hi: чем больше доля, тем ниже порог
    # JSON → строки клеток retitr_cells.py: fa/fo/fv, три среза на признак
    json.loads(json.dumps(feats))
    cells = rc.f_cells(feats)
    assert len(cells) == 15 and cells[0][0] == "F_tpm15_lo_10"
    assert cells[0][1] == "fa=tpm15 fo=lo fv=%s" % feats[0]["cuts"]["10"]
    assert float(cells[0][1].split("fv=")[1]) == feats[0]["cuts"]["10"]                 # число в ключе читается без потерь


# ---- ворота: сравнение с таблицами --------------------------------------------------------------------------------------

def test_compare_values_tolerance_and_nan():
    a = np.array([1.0, 2.0, float("nan"), 5.0, float("nan")])
    b = np.array([1.0 + 5e-10, 2.0, float("nan"), 5.0 + 1e-8, 3.0])
    r = rf.compare_values(a, b)
    assert r["n_common"] == 5 and r["n_both_finite"] == 3 and r["n_both_nan"] == 1 and r["n_nan_mismatch"] == 1
    assert r["n_mismatch"] == 2          # 5 vs 5 + 1e-8 (> 1e-9) и NaN против числа
    assert abs(r["max_abs_diff"] - 1e-8) < 1e-12
    ok = rf.compare_values(np.arange(300.0), np.arange(300.0))
    assert ok["n_mismatch"] == 0 and rf.gate_pass(ok)
    few = rf.compare_values(np.arange(10.0), np.arange(10.0))
    assert not rf.gate_pass(few)         # < 200 общих конечных ключей — ворота не пройдены


def test_match_candidates_identity_not_just_key():
    mine = [("A", 10, 100, 5000), ("A", 20, 200, 5000), ("B", 30, 300, 7000), ("C", 40, 400, 1)]
    ref = [("A", 10, 100, 5000),      # тот же кандидат
           ("A", 20, 999, 4000),      # тот же ключ (sym, sig_ms), другой уровень — не сравнивается, считается отдельно
           ("B", 30, 300, 7000), ("B", 30, 300, 7000)]   # неоднозначная личность — не сравнивается
    pairs, other, amb = rf.match_candidates(mine, ref)
    assert pairs == [(0, 0)] and other == 1 and amb == 2, (pairs, other, amb)


# ---- ворота движка: строки вариантов ------------------------------------------------------------------------------------

def test_gate_cells_lines():
    acc = tmp("acc.json")
    json.dump({"cumulative_keys": "s=4 rr=1 bm=0 t_high=8 q=25 natr_min=0.3", "extend": [], "side": "short"}, open(acc, "w"))
    rows = rf.gate_cells("S", acc)
    assert len(rows) == 1 + 4 * 5
    assert rows[0].startswith("S_base ") and "natr_min=0.3" in rows[0] and "fa=" not in rows[0]
    ids = [r.split()[0] for r in rows]
    assert len(set(ids)) == len(ids)
    pa = [r for r in rows if r.startswith("G_tpm15_pass_lo ")][0]
    assert pa.endswith("fa=tpm15 fo=lo fv=-1e18") and pa.startswith("G_tpm15_pass_lo " + rows[0].split(" ", 1)[1])
    assert any(r.endswith("fa=cons60 fo=hi fv=1e18") and r.startswith("G_cons60_pass_hi ") for r in rows)
    assert any(r.endswith("fa=big1800 fo=lo fv=1e18") and r.startswith("G_big1800_cut_lo ") for r in rows)
    # ключи fa/fo/fv разбираются генератором клеток как одна тройка
    plain, feats = rc.parse_keys(pa.split(" ", 1)[1])
    assert feats == [{"fa": "tpm15", "fo": "lo", "fv": "-1e18"}]
    assert all(float(r.split("fv=")[1]) in (-1e18, 1e18) for r in rows[1:])


def test_parse_gate_id():
    assert rf.parse_gate_id("G_oi_c24_pass_lo") == ("oi_c24", "pass_lo")
    assert rf.parse_gate_id("G_since_born_h_cut_hi") == ("since_born_h", "cut_hi")
    assert rf.parse_gate_id("L_base") is None and rf.parse_gate_id("F_x_lo_10") is None


def test_check_gate_cell_semantics():
    keys = {("A", 1), ("B", 2)}                                        # кандидаты без значения (NaN)
    ok_pass = [100, 0, 0, 2]
    assert rf.check_gate_cell("pass_lo", 100, 2, ok_pass, True, [], keys)
    assert not rf.check_gate_cell("pass_lo", 100, 2, ok_pass, False, [], keys)                    # файлы разошлись
    assert not rf.check_gate_cell("pass_hi", 100, 2, [100, 1, 0, 2], True, [], keys)               # «пропускает всех» отбросил
    assert not rf.check_gate_cell("pass_hi", 100, 2, [100, 0, 3, 2], True, [], keys)               # ключи кандидатов не нашлись в таблице
    assert not rf.check_gate_cell("pass_hi", 100, 2, [99, 0, 0, 2], True, [], keys)                # число кандидатов ≠ строк таблицы
    cut = [100, 98, 0, 2]
    assert rf.check_gate_cell("cut_lo", 100, 2, cut, False, [("A", 1)], keys)                      # сделка только у NaN-кандидата — допустимо
    assert not rf.check_gate_cell("cut_lo", 100, 2, cut, False, [("C", 3)], keys)                  # сделка кандидата со значением — «режет всех» нарушено
    assert not rf.check_gate_cell("cut_hi", 100, 2, [100, 90, 0, 2], False, [], keys)              # отброшено не всё со значением
    assert rf.check_gate_cell("cut_hi", 100, 0, [100, 100, 0, 0], False, [], set())                # признак без NaN: 0 сделок


def test_feature_lists_are_the_fixed_top5():
    assert rf.FEATURES["L"] == [("oi_c24", "hi"), ("maxmv1", "lo"), ("depth_mean_bp", "hi"), ("depth_min_bp", "hi"), ("loi_turn", "lo")]
    assert rf.FEATURES["S"] == [("tpm15", "lo"), ("cons60", "hi"), ("tpm60", "lo"), ("big1800", "lo"), ("since_born_h", "lo")]
    assert rf.CUT_PCTS == (10, 20, 25) and rf.A_DAYS == (14, 43)


if __name__ == "__main__":
    fails = 0
    names = [k for k in sorted(globals()) if k.startswith("test_")]
    for k in names:
        try:
            globals()[k]()
            print("ok  ", k)
        except Exception as e:  # noqa: BLE001
            fails += 1
            print("FAIL", k, repr(e))
    print("%d тестов, %d ошибок" % (len(names), fails))
    sys.exit(1 if fails else 0)


def test_run_tk594_switch_to_rust_by_env(monkeypatch):
    """P42_FEAT_BIN → rustfeat_run.py с теми же аргументами; без переменной — прежний tk594_feat.py (TK-609)."""
    calls = []
    monkeypatch.setattr(rf.subprocess, "run", lambda cmd, check: calls.append(cmd))
    monkeypatch.delenv("P42_FEAT_BIN", raising=False)
    rf.run_tk594("/prod", "e.parquet", "o.parquet", "flow", 6, "2026-07-25")
    assert calls[-1][1].endswith("tk594_feat.py")
    monkeypatch.setenv("P42_FEAT_BIN", "/x/alpha-feat")
    rf.run_tk594("/prod", "e.parquet", "o.parquet", "deriv", 4, "2026-09-01")
    c = calls[-1]
    assert c[1].endswith("rustfeat_run.py") and c[2:4] == ["e.parquet", "o.parquet"]
    assert c[c.index("--mode") + 1] == "deriv" and c[c.index("--base-date") + 1] == "2026-09-01"
    assert c[c.index("--procs") + 1] == "4" and c[c.index("--bin") + 1] == "/x/alpha-feat"
