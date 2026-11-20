"""TK-652 (§13.52): сетка клеток, соседи по `rr`, правило п. 7 / п. 8 на синтетических строках (без srv)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("TK643_ROOT", "/tmp/tk652-test")
import tk652_run as t  # noqa: E402


def row(d, p, lower, n=400, sharpe=3.0, dm=(1, 1, 1, 1, 1)):
    return dict(n=n, months=[(90, 10.0)] * 4 + [(30, 5.0)], dmonths=list(dm), robust_days=d, robust_coins=d, sharpe=sharpe,
                delta=dict(d=d, p=p, lower=lower, s_c=100.0, dsum=d * n, se_sum=10.0))


def test_grid_and_neighbours():
    c = t.cells("L")
    assert [x["cid"] for x in c] == ["tl1_rr1", "tl1_rr2", "tl1_rr3", "tl2_rr1", "tl2_rr2", "tl2_rr3"]
    assert c[4]["patch"] == {"tl": 2}
    allc = {x["cid"]: x for x in c}
    assert t.neighbours(allc["tl2_rr2"], allc) == ["tl2_rr1", "tl2_rr3"]
    assert t.neighbours(allc["tl1_rr1"], allc) == ["tl1_rr2"]


def test_verdict_rules():
    base = {cid: row(0.01, 0.4, -0.02) for cid in [x["cid"] for x in t.cells("L")]}
    rows = {"L": dict(base), "S": dict(base)}
    rows["L"]["tl2_rr2"] = row(0.30, 1e-5, 0.2)
    rows["L"]["tl2_rr1"] = row(0.20, 0.5, -0.1)
    rows["L"]["tl2_rr3"] = row(0.10, 0.5, -0.1)           # сосед < 0,5·Δ → плато нет
    rows["S"]["tl2_rr2"] = row(-0.10, 0.99, -0.12)
    v = t.verdict(rows)
    assert v["m"] == 12 and v["trials_noise"] == 6 + 394
    gl = v["sides"]["L"]["cells"]["tl2_rr2"]["gates"]
    assert gl["b"] and not gl["f"] and not v["sides"]["L"]["passed"]
    assert v["sides"]["L"]["rule_tl2"]["not_worse"] is True
    assert v["sides"]["S"]["rule_tl2"]["not_worse"] is False
