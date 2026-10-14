import os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
LAUNCH = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, LAUNCH)
import launch

CFG = launch.load_config()
M3 = ["2026-01", "2026-02", "2026-03"]
ROWS = [
    {"hyp": "Г-01", "hash": "aaaa", "axes": {"q": "5"}},     # посчитано полностью
    {"hyp": "Г-01", "hash": "bbbb", "axes": {"q": "10"}},    # частично
    {"hyp": "Г-01", "hash": "cccc", "axes": {"q": "20"}},    # не считали
    {"hyp": "Г-02", "hash": "dddd", "axes": {"q": "5"}},     # другая гипотеза
]
HAVE = {"aaaa": set(M3), "bbbb": {"2026-01"}}
LINE = "chase-pct2-tr1x1-14400-ttl1800 t-bid-btc4h-q1"
DOC = {"lines": {"bbbb": LINE, "cccc": LINE}, "monthly": {}, "sets": {"t-bid-btc4h-q1": "btc4h_max=1"}, "r2_re": "(r2x)"}


def p(hyps=("Г-01",), axes=None, months=M3):
    return launch.plan(list(hyps), months, axes, rows=ROWS, have=HAVE, cfg=CFG)


def test_plan_counts_full_partial_none():
    r = p()
    assert (r["grid"], r["have"], r["new"]) == (3, 1, 2)
    assert (r["hm_grid"], r["hm_have"], r["hm_new"]) == (9, 4, 5)


def test_plan_axis_filter_and_coverage():
    r = p(axes={"q": ["5", "10"]})
    assert r["grid"] == 2 and r["hm_new"] == 2
    assert r["axis_cov"]["q"]["10"] == [1, 3] and r["axis_cov"]["q"]["5"] == [3, 3]


def test_plan_month_subset_makes_cell_done():
    r = p(months=["2026-01"])
    assert r["hm_new"] == 1 and r["have"] == 2     # bbbb в январе уже есть


def test_submit_refuses_when_nothing_to_count():
    calls = []
    res = launch.submit(["Г-01"], M3, {"q": ["5"]}, cfg=CFG, p=p(axes={"q": ["5"]}), run_cmd=lambda *a, **k: calls.append(a), log=lambda *a: None)
    assert not res["ok"] and res["why"] == "nothing-to-count" and not calls


def test_submit_dry_run_prints_command_and_sets_nothing():
    calls, out = [], []
    res = launch.submit(["Г-01"], M3, cfg=CFG, p=p(), dry_run=True, run_cmd=lambda *a, **k: calls.append(a), log=out.append, doc=DOC)
    assert res["ok"] and res["dry_run"] and not calls
    assert res["cmd"][:3] == ["python3", "/data/sched/alsched.py", "submit"] and "--cores" in res["cmd"] and "12" in res["cmd"]
    assert "ssh" not in res["cmd"] and res["cmd"][-2].endswith("run-launch.sh")
    assert any("alsched.py submit" in s for s in out)


def test_submit_needs_bin_confirmation():
    calls = []
    res = launch.submit(["Г-01"], M3, cfg=CFG, p=p(), run_cmd=lambda *a, **k: calls.append(a), log=lambda *a: None)
    assert not res["ok"] and res["why"] == "bin-not-confirmed" and not calls


def test_queue_params_only_from_config():
    src = open(os.path.join(LAUNCH, "launch.py"), encoding="utf-8").read()
    assert not re.search(r'"--(cores|mem|est|max-runtime)",\s*"?\d', src)
    assert CFG["queue"] == {"cls": "prod", "cores": 12, "mem": 24, "max_runtime": 60000, "est": 45000}


def test_no_neural_calls_in_code():
    bad = re.compile(r"tickets\.py|claude\s|notify|telegram|api\.telegram|anthropic", re.I)
    for fn in os.listdir(LAUNCH):
        if fn.endswith(".py"):
            code = open(os.path.join(LAUNCH, fn), encoding="utf-8").read()
            code = "\n".join(l for l in code.splitlines() if not l.lstrip().startswith("#"))
            assert not bad.search(code.split('"""', 2)[-1]), fn


def test_plan_splits_off_routes_from_to_count():
    routes = {"bbbb": "cells", "cccc": "outside", "aaaa": "cells"}
    r = launch.plan(["Г-01"], M3, rows=ROWS, have=HAVE, cfg=CFG, routes=routes)
    assert (r["hm_grid"], r["hm_have"], r["hm_new"], r["hm_off"]) == (9, 4, 2, 3)
    assert r["off_by"] == {"outside": 3} and r["hm_grid"] == r["hm_have"] + r["hm_new"] + r["hm_off"]
    assert "вне формы 3 (outside 3)" in launch.summary(r)


def test_submit_refuses_when_only_off_cells_left():
    r = launch.plan(["Г-01"], M3, {"q": ["20"]}, rows=ROWS, have=HAVE, cfg=CFG, routes={"cccc": "r1route"})
    assert r["hm_new"] == 0 and r["hm_off"] == 3
    res = launch.submit(["Г-01"], M3, {"q": ["20"]}, cfg=CFG, p=r, run_cmd=lambda *a, **k: 1 / 0, log=lambda *a: None)
    assert not res["ok"] and res["why"] == "nothing-to-count"


def test_routes_file_covers_grid_and_known_routes():
    routes = launch.load_routes()
    assert {r["hash"] for r in launch.plan_rows()} <= set(routes)
    assert set(routes.values()) <= {"cells", "monthly", "outside", "nodata", "noline", "r1route"}


def test_prepare_plan_writes_per_month_cells_args_bin(tmp_path):
    per, r2 = launch.prepare_plan(p(), str(tmp_path), DOC)
    assert per == {"2026-01": 1, "2026-02": 1, "2026-03": 1} and not r2   # bbbb в январе уже есть; одинаковые строки --cells сливаются
    assert (tmp_path / "L-2026-02.cells").read_text(encoding="utf-8") == LINE + "\n"
    assert "--set t-bid-btc4h-q1:btc4h_max=1" in (tmp_path / "L-2026-02.args").read_text(encoding="utf-8")
    assert (tmp_path / "L-2026-02.bin").read_text() == "std\n" and (tmp_path / "L.cells").read_text() == ""
    assert (tmp_path / "months.txt").read_text() == "2026-01\n2026-02\n2026-03\n"


def test_submit_over_cap_and_r2_and_not_on_calc():
    big = dict(CFG, max_cells_per_run=1)
    assert launch.submit(["Г-01"], M3, cfg=big, p=p(), dry_run=True, log=lambda *a: None, doc=DOC)["why"] == "over-cap"
    r2doc = dict(DOC, lines={"bbbb": LINE.replace("ttl1800", "ttl1800-r2x"), "cccc": LINE.replace("ttl1800", "ttl1800-r2x")}, r2_re="(r2x)")
    nor2 = dict(CFG, bin=dict(CFG["bin"], features=""))
    assert launch.submit(["Г-01"], M3, cfg=nor2, p=p(), dry_run=True, log=lambda *a: None, doc=r2doc)["why"] == "r2-bin-needed"
    off = dict(CFG, alsched="/нет/такого/alsched.py")
    assert launch.submit(["Г-01"], M3, cfg=off, p=p(), confirm_bin=True, log=lambda *a: None, doc=DOC)["why"] == "not-on-calc"
