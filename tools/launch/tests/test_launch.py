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
    res = launch.submit(["Г-01"], M3, cfg=CFG, p=p(), dry_run=True, run_cmd=lambda *a, **k: calls.append(a), log=out.append)
    assert res["ok"] and res["dry_run"] and not calls
    assert res["cmd"][:3] == ["ssh", "calc", "python3"] and "--cores" in res["cmd"] and "12" in res["cmd"]
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
