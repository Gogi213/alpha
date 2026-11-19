"""Тесты §13.33 (TK-633): «чужая» история (события противоположной стороны), обратный фильтр, порядок и дубли клеток, неудачные пробои, совместный Холм, исход единиц. Синтетика, без движка, без P&L.
Без pytest: `PYTHONUTF8=1 python3 tools/p42/tests/test_tk633.py` — выполняет все test_* и печатает итог.
"""
import datetime
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
import tk625_run as r5  # noqa: E402
import tk625_stats as s5  # noqa: E402
import tk633_run as r6  # noqa: E402

MIN = 60_000
DAY = 86_400_000


def ms(date):
    return int(datetime.datetime.fromisoformat(date).replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)


def mk_events(rows):
    """rows: (side, sym, cross_ms, {m: (код, t_res)}) → Events."""
    n = len(rows)
    code = {m: np.array([r[3].get(m, ("", -1))[0] for r in rows], dtype=object) for m in f5.MS_NAMES}
    tres = {m: np.array([r[3].get(m, ("", -1))[1] for r in rows], dtype=np.int64) for m in f5.MS_NAMES}
    return f5.Events(np.array([r[0] for r in rows], dtype=object), np.array([r[1] for r in rows], dtype=object), np.array([r[2] - MIN for r in rows], dtype=np.int64),
                     np.ones(n, dtype=np.int8), np.ones(n, dtype=np.int8), np.array([r[2] for r in rows], dtype=np.int64), code, tres)


T0 = ms("2026-08-01")


class FakeUni:
    name = "fake"

    def coins(self):
        return ["AAA"]


def install(rows):
    """Подставить события и сутки монеты вместо чтения диска; возвращает вселенную."""
    r6._C.pop(("histx", "fake", "L"), None)
    r6._C.pop(("histx", "fake", "S"), None)
    r5._C[("ev", "fake")] = (None, f5.dedup_crossings(mk_events(rows)))
    r5._C[("cd", "fake")] = {"AAA": np.ones(150, dtype=bool)}
    return FakeUni()


def count(u, side, t, d=7, m="10"):
    return int(r6.hist_x(u, side).counts(np.array(["AAA"], dtype=object), np.array([t]), d, m)[0][0])


# ---- чужая история ---------------------------------------------------------------------------------------------------------------

def test_cross_history_uses_opposite_side_events():
    c1, c2, c3 = T0 - 1 * DAY, T0 - 2 * DAY, T0 - 3 * DAY
    u = install([("long", "AAA", c1, {"10": ("o", c1 + 5 * MIN)}),
                 ("short", "AAA", c2, {"10": ("o", c2 + 5 * MIN)}),
                 ("short", "AAA", c3, {"10": ("o", c3 + 5 * MIN)})])
    assert count(u, "L", T0) == 2          # лонг-сделка видит шорт-историю
    assert count(u, "S", T0) == 1          # шорт-сделка видит лонг-историю
    own_l = f5.History(r5._C[("ev", "fake")][1].take(r5._C[("ev", "fake")][1].side == "long"), r5._C[("cd", "fake")], ["AAA"])
    assert int(own_l.counts(np.array(["AAA"], dtype=object), np.array([T0]), 7, "10")[0][0]) == 1     # «своя» история лонга = long-события (§13.32), не смешана


def test_cross_history_is_causal_and_class_specific():
    c = T0 - 15 * MIN
    u = install([("short", "AAA", c, {"10": ("o", c + 20 * MIN)}), ("short", "AAA", T0 - 2 * DAY, {"10": ("f", T0 - 2 * DAY + 5 * MIN)})])
    assert count(u, "L", T0) == 0                                      # первое не закрыто к T0, второе — «возврат», не успех
    assert count(u, "L", T0 + 30 * MIN) == 1
    h = r6.hist_x(u, "L")
    assert int(h.counts(np.array(["AAA"], dtype=object), np.array([T0]), 7, "10")[1][0]) == 1           # n_fail чужой стороны


def test_all_cols_names_match_ftab_columns():
    assert r6.NAMES[:len(f5.COLS)] == f5.COLS and r6.NAMES[len(f5.COLS):] == ["x" + c for c in f5.COLS] and len(r6.NAMES) == 18
    cols = {c: np.array([1.0, np.nan, 3.0]) for c in r6.NAMES}
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "t.csv")
        n = r6.write_ftab(p, np.array(["B", "A", "A"], dtype=object), np.array([5, 7, 7]), cols)
        lines = open(p, encoding="utf-8").read().splitlines()
        tab = r6.read_table(p)
    assert n == 2 and lines[0].split(",") == ["sym", "sig_ms"] + r6.NAMES and lines[1].startswith("A,7,,,") and len(tab) == 2
    assert np.isnan(tab[("A", 7)][0]) and tab[("B", 5)][0] == 1.0 and tab[("B", 5)][2] == 1.0          # дубль ключа — первая строка; значения читаются


# ---- фильтры, клетки -------------------------------------------------------------------------------------------------------------

def test_excluded_hi_nan_passes():
    x = np.array([0, 1, 2, np.nan])
    assert r6.excluded_hi(x, 0).tolist() == [False, True, True, False]
    assert f5.excluded(x, 1).tolist() == [True, False, False, False]
    assert r6.dropped_by(1.0, 0, "hi") and not r6.dropped_by(0.0, 0, "hi") and not r6.dropped_by(float("nan"), 0, "hi") and r6.dropped_by(0.0, 1, "lo")


def test_fail_buckets():
    x = np.array([0, 1, 2, 3, 5, 6, 10, 11, 40, np.nan])
    assert r6.fail_bucket_index(x).tolist() == [0, 1, 1, 2, 2, 3, 3, 4, 4, 5]


def fake_x(n=200, seed=1):
    rng = np.random.default_rng(seed)
    out = {}
    for nm in r6.NAMES:
        v = rng.poisson(1.5, n).astype(float)
        v[rng.random(n) < 0.1] = np.nan
        out[nm] = v
    return out


def test_cell_order_and_counts():
    ol, os_ = r6.cell_order("L"), r6.cell_order("S")
    assert [c for c in ol if c[4]] == [("X", "10", d, qq, True) for d in (7, 14, 30) for qq in (0.15, 0.30)] + [("I", "10", 7, -1.0, True), ("I", "10", 14, -1.0, True)]
    assert len([c for c in os_ if c[4]]) == 6 and all(c[0] == "X" for c in os_)                  # у шорта обратного фильтра нет
    assert len([c for c in ol if not c[4]]) == 12 + 4 and len([c for c in os_ if not c[4]]) == 12  # соседи: m = 0,5 и 2


def test_build_cells_inverse_and_cross_thresholds():
    x = fake_x()
    cells = r6.build_cells("L", x, x)
    byid = {c["cid"]: c for c in cells}
    i7 = byid["I_m10_d7"]
    assert i7["fo"] == "hi" and i7["c"] == 0 and i7["col"] == "ok10_d7" and i7["q"] == -1.0 and i7["trial"]
    assert i7["E"] == int(((~np.isnan(x["ok10_d7"])) & (x["ok10_d7"] > 0)).sum())
    x14 = byid["X_m10_d14_q15"]
    assert x14["fo"] == "lo" and x14["col"] == "xok10_d14" and x14["c"] == f5.threshold_for(x["xok10_d14"], 0.15)
    assert byid["X_m05_d14_q15"]["trial"] is False
    for c in cells:                                                                                  # дубль = то же множество исключённых
        if c["dup_of"] not in (None, "нет порога"):
            assert np.array_equal(c["mask"], byid[c["dup_of"]]["mask"])


def test_build_cells_dup_and_cmap():
    x = {nm: np.zeros(100) for nm in r6.NAMES}
    x["xok10_d7"] = np.array([0.0] * 60 + [1.0] * 40)                                                # F(x<1) = 0,6 ≥ 0,15 и ≥ 0,30 → c = 1 в обеих → дубль
    cells = r6.build_cells("S", x, x)
    byid = {c["cid"]: c for c in cells}
    assert byid["X_m10_d7_q15"]["c"] == 1 and byid["X_m10_d7_q30"]["c"] == 1 and byid["X_m10_d7_q30"]["dup_of"] == "X_m10_d7_q15" and byid["X_m10_d7_q15"]["dup_of"] is None
    cm = {c["cid"]: c["c"] for c in cells}
    cells2 = r6.build_cells("S", x, x, cm)
    assert [c["c"] for c in cells2] == [c["c"] for c in cells] and [c["dup_of"] for c in cells2] == [c["dup_of"] for c in cells]
    cells3 = r6.build_cells("S", x, x, {})
    assert all(c["c"] is None and c["dup_of"] == "нет порога" for c in cells3)


def test_unit_names_and_outcomes():
    assert r6.unit_of("L", "X_m10_d7_q15") == "ЛШ" and r6.unit_of("S", "X_m10_d7_q15") == "ШЛ" and r6.unit_of("L", "I_m10_d7") == "ЛЛ-обр"
    row = lambda d, adm=True: dict(trial=True, admitted=adm, d=d)                                    # noqa: E731
    ev = dict(passed=[("L", "X_a")], rows={"L": {"X_a": row(0.5), "I_a": row(-0.1)}, "S": {"X_b": row(-0.2), "X_c": row(-0.3), "X_n": dict(trial=False, admitted=True, d=0.9)}})
    assert r6.unit_outcome(ev, {("L", "X_a"): dict(repl=True)}, "ЛШ") == ("работает", ["X_a"])
    assert r6.unit_outcome(ev, {("L", "X_a"): dict(repl=False)}, "ЛШ")[0] == "не ясно"                # прошла, но не повторилась
    assert r6.unit_outcome(ev, {}, "ШЛ") == ("не работает", [])                                       # все допущенные пробные Δ < 0; сосед не считается
    assert r6.unit_outcome(ev, {}, "ЛЛ-обр") == ("не работает", [])
    ev["rows"]["S"]["X_b"] = row(0.1)
    assert r6.unit_outcome(ev, {}, "ШЛ")[0] == "не ясно"
    ev["rows"]["S"] = {}
    assert r6.unit_outcome(ev, {}, "ШЛ")[0] == "не ясно"                                              # нет допущенных — не «не работает»
    assert r6.answer_line({"ЛШ": ("работает", ["a"]), "ШЛ": ("не ясно", []), "ЛЛ-обр": ("не работает", [])}) == "работает (ЛШ)"
    assert r6.answer_line({"ЛШ": ("не работает", []), "ШЛ": ("не работает", []), "ЛЛ-обр": ("не работает", [])}) == "не работает"
    assert r6.answer_line({"ЛШ": ("не работает", []), "ШЛ": ("не ясно", []), "ЛЛ-обр": ("не работает", [])}) == "не ясно"
    ev["rows"]["L"]["I_a"] = row(0.4)                                                                  # ЛЛ-обр прошла g1–g5 и o566: кандидат для вперёд, не «работает»
    ev["passed"] = [("L", "I_a")]
    assert r6.unit_outcome(ev, {("L", "I_a"): dict(repl=True)}, "ЛЛ-обр") == (r6.CANDIDATE, ["I_a"])
    assert r6.answer_line({"ЛШ": ("не работает", []), "ШЛ": ("не работает", []), "ЛЛ-обр": (r6.CANDIDATE, ["I_a"])}).startswith("не ясно; ЛЛ-обр — кандидат для вперёд")
    assert r6.answer_line({"ЛШ": ("работает", ["a"]), "ШЛ": ("не ясно", []), "ЛЛ-обр": (r6.CANDIDATE, ["I_a"])}).startswith("работает (ЛШ); ЛЛ-обр — кандидат")
    assert "работает (ЛЛ-обр" not in r6.answer_line({"ЛШ": ("не ясно", []), "ШЛ": ("не ясно", []), "ЛЛ-обр": (r6.CANDIDATE, ["I_a"])})


def test_joint_holm_stricter_than_new_only():
    ev = dict(holm=dict(m=2, ps=[0.006, 0.2], trial=[("L", "X_a"), ("S", "X_b")]),
              rows={"L": {"X_a": dict(admitted=True, d=0.5, g2=True, g3=True, g4=True, g5=True)}, "S": {"X_b": dict(admitted=True, d=0.1, g2=True, g3=True, g4=True, g5=True)}}, passed=[])
    old = [0.5] * 9                                                                                    # 9 пробных клеток §13.32: p как есть
    out = r6.joint_holm(ev, old)
    ra = out["rows"]["L"]["X_a"]
    assert out["holm"]["m_joint"] == 11 and ra["g1_new_only"] is True                                  # по одним новым: 0,006 ≤ 0,05 / 2
    assert ra["g1"] is False and ra["pass"] is False and out["passed"] == []                           # совместно: 0,006 > 0,05 / 11
    ev2 = dict(holm=dict(m=1, ps=[0.004], trial=[("L", "I_a")]), rows={"L": {"I_a": dict(admitted=True, d=0.5, g2=True, g3=True, g4=True, g5=True)}}, passed=[])
    out2 = r6.joint_holm(ev2, old)
    assert out2["rows"]["L"]["I_a"]["g1"] is True and out2["passed"] == [("L", "I_a")]                 # 0,004 ≤ 0,05 / 10
    ev3 = dict(holm=dict(m=1, ps=[1.0], trial=[("L", "I_a")]), rows={"L": {"I_a": dict(admitted=False, d=0.5, g2=True, g3=True, g4=True, g5=True)}}, passed=[])
    assert r6.joint_holm(ev3, old)["passed"] == []                                                      # недопущенная — p = 1, не проходит


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
