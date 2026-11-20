"""Тесты §13.34 (TK-634): компоненты IMP / FLOW, ECDF с ничьими, правило определённости композита, 22 клетки на сторону, NaN проходит, пороги fv, таблица --ftab,
контраст взаимодействия, совместный Холм m = 21 + 8, исход единиц. Синтетика, без движка, без P&L.
Без pytest: `PYTHONUTF8=1 python3 tools/p42/tests/test_tk634.py` — выполняет все test_* и печатает итог.
"""
import os
import sys
import tempfile
import traceback

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
os.environ.setdefault("SIGFEAT_BASE_DATE", "2026-06-17")
os.environ.setdefault("SIGFEAT_DAYS", "80")
import tk604_stats as st  # noqa: E402
import tk625_feat as f5  # noqa: E402
import tk634_run as r6  # noqa: E402

NAN = float("nan")


def eq(a, b):
    return np.array_equal(np.asarray(a, dtype=float), np.asarray(b, dtype=float), equal_nan=True)


# ---- компоненты ------------------------------------------------------------------------------------------------------------------

def raw_of(**kw):
    n = max(len(np.atleast_1d(v)) for v in kw.values())
    base = {k: np.full(n, 1.0) for k in ("mv5", "mv15", "mv60", "turn1h", "turn24h", "tfv300", "big300", "msz_r", "acc_n")}
    base.update({k: np.asarray(v, dtype=float) for k, v in kw.items()})
    return base


def test_components_formulas():
    c = r6.components(raw_of(mv5=[5.0, 10.0], mv60=[60.0, 15.0], turn1h=[2.0, 0.0], turn24h=[24.0, 48.0]))
    assert np.allclose(c["acc_px"], [5 / 5 - 55 / 55, 10 / 5 - 5 / 55])             # mv5/5 − (mv60 − mv5)/55
    assert np.allclose(c["vsurge"], [2.0 * 24 / 24, 0.0])                            # turn1h · 24 / turn24h
    assert set(c) == set(r6.COMPS["IMP"]) | set(r6.COMPS["FLOW"])


def test_components_nan_and_zero_turnover():
    c = r6.components(raw_of(mv5=[1.0, np.nan, 1.0], mv60=[1.0, 1.0, np.inf], turn1h=[1.0, 1.0, 1.0], turn24h=[0.0, -1.0, 1.0], mv15=[np.inf, 1.0, 1.0]))
    assert np.isnan(c["vsurge"][0]) and np.isnan(c["vsurge"][1]) and np.isfinite(c["vsurge"][2])      # суточный оборот ≤ 0 → не определено
    assert np.isnan(c["acc_px"][1]) and np.isnan(c["acc_px"][2])                                        # NaN и inf → не определено
    assert np.isnan(c["mv15"][0]) and c["mv15"][1] == 1.0                                               # inf сырого столбца → NaN


# ---- ECDF и композит -------------------------------------------------------------------------------------------------------------

def test_ecdf_midrank_ties():
    ref = np.array([1.0, 2.0, 2.0, 3.0])
    assert r6.ecdf_pct(ref, [0.0, 1.0, 2.0, 3.0, 4.0]).tolist() == [0.0, 0.125, 0.5, 0.875, 1.0]       # (#< + #≤) / (2N)
    out = r6.ecdf_pct(ref, [np.nan, 2.0])
    assert np.isnan(out[0]) and out[1] == 0.5
    assert np.isnan(r6.ecdf_pct(np.array([]), [1.0])[0])                                                # пустой эталон → NaN


def test_make_refs_sorted_finite():
    refs = r6.make_refs({"a": np.array([3.0, np.nan, 1.0, 2.0])})
    assert refs["a"].tolist() == [1.0, 2.0, 3.0]


def refs_all(arr):
    return {n: np.array(arr, dtype=float) for names in r6.COMPS.values() for n in names}


def test_composite_mean_of_percent_ranks():
    refs = refs_all([1.0, 2.0, 3.0, 4.0])
    comps = {n: np.array([4.0]) for n in r6.COMPS["IMP"]}
    comps["vsurge"] = np.array([1.0])
    assert np.allclose(r6.composite(comps, refs, "IMP"), [(0.875 + 0.875 + 0.125) / 3])


def test_composite_availability_rule():
    refs = refs_all([1.0, 2.0, 3.0, 4.0])
    imp = {"mv15": np.array([4.0, 4.0, np.nan]), "acc_px": np.array([1.0, np.nan, np.nan]), "vsurge": np.array([np.nan, np.nan, 4.0])}
    out = r6.composite(imp, refs, "IMP")
    assert np.isclose(out[0], (0.875 + 0.125) / 2)                                                     # две из трёх — определён, среднее по двум
    assert np.isnan(out[1]) and np.isnan(out[2])                                                       # одна из трёх — нет
    flow = {n: np.array([4.0, 4.0, 4.0]) for n in r6.COMPS["FLOW"]}
    flow["acc_n"] = np.array([np.nan, np.nan, np.nan])
    flow["msz_r"] = np.array([np.nan, 4.0, np.nan])
    flow["big300"] = np.array([np.nan, 4.0, np.nan])
    out = r6.composite(flow, refs, "FLOW")
    assert np.isnan(out[0]) and np.isnan(out[2])                                                       # одна из четырёх — нет
    assert np.isclose(out[1], 0.875)                                                                   # три из четырёх (tfv300, msz_r, big300) — да
    flow2 = {n: np.array([4.0]) for n in r6.COMPS["FLOW"]}
    flow2["acc_n"] = np.array([np.nan])
    assert np.isclose(r6.composite(flow2, refs, "FLOW")[0], 0.875)                                     # ровно три — порог


def test_fv_table_quantiles_ignore_nan():
    v = np.concatenate([np.linspace(0.0, 1.0, 101), [np.nan] * 50])
    fv = r6.fv_table({"IMP": v})
    assert set(fv["IMP"]) == {"0.25", "0.10", "0.40"}
    assert np.isclose(fv["IMP"]["0.25"], 0.25) and np.isclose(fv["IMP"]["0.10"], 0.10) and np.isclose(fv["IMP"]["0.40"], 0.40)


# ---- клетки ----------------------------------------------------------------------------------------------------------------------

def test_cell_specs_22_per_side():
    for side in ("L", "S"):
        sp = r6.cell_specs(side)
        assert len(sp) == 22 and len({s["cid"] for s in sp}) == 22
        assert [s["kind"] for s in sp].count("T") == 4 and [s["kind"] for s in sp].count("N") == 16 and [s["kind"] for s in sp].count("R") == 2
        trial = [s for s in sp if s["kind"] == "T"]
        assert {s["cid"] for s in trial} == {"IMP_own", "IMP_cross", "FLOW_own", "FLOW_cross"} and all(s["q"] == 0.25 and s["m"] == "10" for s in trial)
        for t in trial:
            nb = [s for s in sp if s["kind"] == "N" and s["group"] == t["group"]]
            assert sorted(s["cid"] for s in nb) == sorted("%s_%s" % (t["cid"], x) for x in ("q10", "q40", "m05", "m20"))
            assert {(s["q"], s["m"]) for s in nb} == {(0.10, "10"), (0.40, "10"), (0.25, "05"), (0.25, "20")}
            assert all(s["fam"] == t["fam"] and s["ctx"] == t["ctx"] for s in nb)
        ref = [s for s in sp if s["kind"] == "R"]
        assert [s["cid"] for s in ref] == ["IMP_only", "FLOW_only"] and all(s["ctx"] is None and s["m"] is None for s in ref)
        assert len({s["group"] for s in sp if s["kind"] != "N"}) == 6                                  # 4 пробных + 2 справочных группы


def install_cmap(c_own=1, c_cross=1, c_nb=1):
    own = {"L": {"m10_d14_q15": c_own, "m05_d14_q15": c_nb, "m20_d14_q15": c_nb}}
    cross = {"L": {"X_m10_d14_q15": c_cross, "X_m05_d14_q15": c_nb, "X_m20_d14_q15": c_nb}}
    r6._C["cm"] = (own, cross)


def test_hist_col_and_c():
    install_cmap(c_own=1, c_cross=2, c_nb=3)
    assert r6.hist_col("own", "10") == "ok10_d14" == f5.col_name("10", 14) and r6.hist_col("cross", "05") == "xok05_d14"
    assert r6.hist_c("L", "own", "10") == 1 and r6.hist_c("L", "cross", "10") == 2 and r6.hist_c("L", "cross", "20") == 3


def test_excl_mask_nan_passes_and_both_conditions():
    install_cmap(1, 1, 1)
    sp = {"cid": "IMP_own", "fam": "IMP", "ctx": "own", "q": 0.25, "m": "10"}
    fv = {"IMP": {"0.25": 0.4, "0.10": 0.2, "0.40": 0.6}}
    hist = {"ok10_d14": np.array([1.0, 1.0, 1.0, NAN, NAN, 0.0, NAN])}
    comp = {"IMP": np.array([0.1, 0.5, NAN, 0.39, 0.4, 0.9, NAN])}
    # строки: композит ниже fv | оба проходят | композит пуст | история пуста, композит ниже | граница fv проходит (строго <) | история 0 < c | оба пусты
    assert r6.excl_mask("L", sp, hist, comp, fv).tolist() == [True, False, False, True, False, True, False]


def test_excl_mask_neighbors_and_family_only():
    install_cmap(1, 1, 1)
    fv = {"IMP": {"0.25": 0.4, "0.10": 0.2, "0.40": 0.6}}
    hist = {"ok10_d14": np.array([1.0, 1.0, 1.0])}
    comp = {"IMP": np.array([0.15, 0.3, 0.5])}
    mk = lambda q_, ctx="own": r6.excl_mask("L", {"cid": "x", "fam": "IMP", "ctx": ctx, "q": q_, "m": "10"}, hist, comp, fv)       # noqa: E731
    assert mk(0.10).tolist() == [True, False, False] and mk(0.25).tolist() == [True, True, False] and mk(0.40).tolist() == [True, True, True]
    only = r6.excl_mask("L", {"cid": "IMP_only", "fam": "IMP", "ctx": None, "q": 0.25, "m": None}, {}, comp, fv)                       # без истории — столбцы истории не читаются
    assert only.tolist() == [True, True, False]


def test_write_cell_table_nan_blank_and_order():
    cols = {"IMP_own": np.array([1.0, 0.0, 1.0]), "FLOW_own": np.array([0.0, 1.0, 1.0])}
    extra = {"imp_rk": np.array([0.5, NAN, 0.125]), "flow_rk": np.array([NAN, NAN, 0.75])}
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "t.csv")
        n = r6.write_cell_table(p, np.array(["A", "A", "B"], dtype=object), np.array([5, 7, 9]), cols, extra)
        lines = open(p, encoding="utf-8").read().splitlines()
    assert n == 3 and lines[0] == "sym,sig_ms,IMP_own,FLOW_own,imp_rk,flow_rk"
    assert lines[1] == "A,5,1.0,0.0,0.5," and lines[2] == "A,7,0.0,1.0,," and lines[3] == "B,9,1.0,1.0,0.125,0.75"


# ---- ступень 1: квартили, квадранты, контраст взаимодействия ----------------------------------------------------------------------

def test_quartile_index_edges():
    edges = [1.0, 2.0, 3.0]
    assert r6.quartile_index(np.array([0.5, 1.0, 1.5, 2.0, 3.0, 3.1, NAN]), edges).tolist() == [0, 0, 1, 1, 2, 3, 4]
    h = np.array([1.0, 2.0, 3.0, 4.0, NAN])
    assert r6.quartile_edges(h) == [float(np.quantile([1, 2, 3, 4], x)) for x in (0.25, 0.5, 0.75)]


def test_month_sharpes_zero_days_and_nan():
    day = np.array([0, 1])
    s = r6.month_sharpes(day, np.array([1.0, 3.0]))
    assert len(s) == len(st.MONTH_LEN)
    x = np.zeros(31)
    x[:2] = [1.0, 3.0]
    assert np.isclose(s[0], x.mean() / x.std(ddof=1) * np.sqrt(365)) and all(np.isnan(v) for v in s[1:])        # месяцы без P&L — sd = 0 → NaN


def quad_data(vals=(5.0, 1.0, 2.0, 0.0), per_day=1):
    """Каждая сутки — по per_day сделок в каждом квадранте (ИС, И¬С, ¬ИС, ¬И¬С) со значениями vals."""
    day, val, hp, fp = [], [], [], []
    for d in range(st.NDAYS):
        for _ in range(per_day):
            for (h_, f_), v in zip(((1, 1), (1, 0), (0, 1), (0, 0)), vals):
                day.append(d), val.append(v), hp.append(bool(h_)), fp.append(bool(f_))
    return np.array(day), np.array(val), np.array(hp), np.array(fp)


def test_interaction_contrast_exact():
    day, val, hp, fp = quad_data()
    r = r6.interaction(day, val, hp, fp, nboot=200)
    assert r["means"] == [5.0, 1.0, 2.0, 0.0] and r["n"] == [st.NDAYS] * 4
    assert np.isclose(r["contrast"], (5 - 1) - (2 - 0)) and np.isclose(r["lo90"], 2.0) and np.isclose(r["hi90"], 2.0)       # постоянные средние → интервал вырожден
    day, val, hp, fp = quad_data(vals=(1.0, 1.0, 1.0, 1.0))
    assert np.isclose(r6.interaction(day, val, hp, fp, nboot=200)["contrast"], 0.0)


def test_interaction_empty_quadrant():
    day, val, hp, fp = quad_data()
    keep = ~(hp & ~fp)
    r = r6.interaction(day[keep], val[keep], hp[keep], fp[keep], nboot=50)
    assert r["contrast"] is None and r["lo90"] is None and r["n"][1] == 0


def test_interaction_deterministic():
    rng = np.random.default_rng(3)
    n = 800
    day, val = rng.integers(0, st.NDAYS, n), rng.normal(0, 1, n)
    hp, fp = rng.random(n) < 0.6, rng.random(n) < 0.5
    a, b = r6.interaction(day, val, hp, fp, nboot=300), r6.interaction(day, val, hp, fp, nboot=300)
    assert a == b and a["lo90"] <= a["contrast"] <= a["hi90"] + 1e-9 or a["lo90"] is None


# ---- выбор: совместный Холм, исход единиц ---------------------------------------------------------------------------------------

def mk_row(d, adm=True, trial=True, **g):
    r = dict(trial=trial, admitted=adm, d=d, g2=True, g3=True, g4=True, g5=True)
    r.update(g)
    return r


def g6_ok(ok=True):
    return dict(history=dict(d=0.1, se=0.2), family=dict(d=0.3, se=0.2), ok=ok)


def test_joint_holm_m29_and_g6():
    trial = [(s, c) for s in ("L", "S") for c in ("IMP_own", "IMP_cross", "FLOW_own", "FLOW_cross")]
    ps = [0.0015] + [1.0] * 7
    ev = dict(holm=dict(m=8, ps=ps, trial=trial), rows={"L": {}, "S": {}}, passed=[])
    for s, c in trial:
        ev["rows"][s][c] = mk_row(0.5)
    out = r6.joint_holm(ev, [0.5] * 21, {"L": {"IMP_own": g6_ok()}, "S": {}})
    assert out["holm"]["m_joint"] == 29 and out["holm"]["ps_old"] == [0.5] * 21
    ra = out["rows"]["L"]["IMP_own"]
    assert ra["g1"] and ra["g1_new_only"] and ra["g6"] and ra["pass"] and out["passed"] == [("L", "IMP_own")]          # 0,0015 ≤ 0,05 / 29
    assert "0.100" in ra["g6_note"] and "0.300" in ra["g6_note"]
    rb = out["rows"]["L"]["IMP_cross"]
    assert not rb["g1"] and not rb["g6"] and rb["g6_note"] == "—" and not rb["pass"]                                   # нет ряда g6 — не проходит


def test_joint_holm_stricter_than_new_only_and_g6_blocks():
    trial = [("L", "IMP_own")] + [("S", "IMP_own")] * 0
    ev = dict(holm=dict(m=1, ps=[0.002], trial=trial), rows={"L": {"IMP_own": mk_row(0.5)}}, passed=[])
    out = r6.joint_holm(ev, [0.5] * 21, {"L": {"IMP_own": g6_ok()}})
    ra = out["rows"]["L"]["IMP_own"]
    assert ra["g1"] is True and out["holm"]["m_joint"] == 22                                                           # 0,002 ≤ 0,05 / 22
    ev = dict(holm=dict(m=8, ps=[0.002] + [1.0] * 7, trial=[("L", c) for c in ("IMP_own", "IMP_cross", "FLOW_own", "FLOW_cross")] + [("S", c) for c in ("IMP_own", "IMP_cross", "FLOW_own", "FLOW_cross")]),
              rows={"L": {c: mk_row(0.5) for c in ("IMP_own", "IMP_cross", "FLOW_own", "FLOW_cross")}, "S": {c: mk_row(0.5) for c in ("IMP_own", "IMP_cross", "FLOW_own", "FLOW_cross")}}, passed=[])
    out = r6.joint_holm(ev, [0.5] * 21, {"L": {"IMP_own": g6_ok()}, "S": {}})
    ra = out["rows"]["L"]["IMP_own"]
    assert ra["g1_new_only"] is True and ra["g1"] is False and not ra["pass"] and out["passed"] == []                  # 0,002 > 0,05 / 29, но ≤ 0,05 / 8
    ev["rows"]["L"]["IMP_own"] = mk_row(0.5)
    ev["holm"]["ps"][0] = 0.0001
    out = r6.joint_holm(ev, [0.5] * 21, {"L": {"IMP_own": g6_ok(False)}, "S": {}})
    assert out["rows"]["L"]["IMP_own"]["g1"] and not out["rows"]["L"]["IMP_own"]["pass"]                                # g6 не выполнена — не проходит
    ev["rows"]["L"]["IMP_own"] = mk_row(0.5, adm=False)
    out = r6.joint_holm(ev, [0.5] * 21, {"L": {"IMP_own": g6_ok()}, "S": {}})
    assert not out["rows"]["L"]["IMP_own"]["pass"]                                                                     # недопущенная — не проходит


def test_unit_outcome_and_answer_line():
    ev = dict(passed=[("L", "IMP_own")], rows={"L": {"IMP_own": mk_row(0.5), "IMP_cross": mk_row(-0.1), "IMP_q10": mk_row(0.9, trial=False),
                                                     "FLOW_own": mk_row(-0.2), "FLOW_cross": mk_row(-0.3)},
                                               "S": {"IMP_own": mk_row(-0.2), "IMP_cross": mk_row(0.1), "FLOW_own": mk_row(-0.2, adm=False), "FLOW_cross": mk_row(-0.3, adm=False)}})
    assert r6.unit_outcome(ev, {("L", "IMP_own"): dict(repl=True)}, "L", "IMP") == ("работает", ["IMP_own"])
    assert r6.unit_outcome(ev, {("L", "IMP_own"): dict(repl=False)}, "L", "IMP")[0] == "не ясно"           # прошла, но не повторилась на o566
    assert r6.unit_outcome(ev, {}, "L", "FLOW") == ("не работает", [])                                       # все допущенные пробные Δ < 0
    assert r6.unit_outcome(ev, {}, "S", "IMP")[0] == "не ясно"                                               # одна Δ > 0
    assert r6.unit_outcome(ev, {}, "S", "FLOW")[0] == "не ясно"                                              # нет допущенных — не «не работает»
    out = {(s, f): ("не работает", []) for s, f in r6.UNITS}
    assert r6.answer_line(out) == "не работает"
    out[("L", "IMP")] = ("не ясно", [])
    assert r6.answer_line(out) == "не ясно"
    out[("S", "FLOW")] = ("работает", ["FLOW_own"])
    assert r6.answer_line(out) == "работает (%s)" % r6.UNIT_NAME[("S", "FLOW")]
    assert len(r6.UNITS) == 4 and len({r6.UNIT_NAME[u] for u in r6.UNITS}) == 4


def test_code_id_is_stable_hex():
    a = r6.code_id()
    assert len(a) == 12 and int(a, 16) >= 0 and a == r6.code_id()
    assert r6.stamp({})["code_id"] == a


def main():
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    fails = 0
    for name, fn in tests:
        try:
            fn()
            print("ok   ", name)
        except Exception:  # noqa: BLE001
            fails += 1
            print("FAIL ", name)
            traceback.print_exc()
    print("%d из %d зелёных" % (len(tests) - fails, len(tests)))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
