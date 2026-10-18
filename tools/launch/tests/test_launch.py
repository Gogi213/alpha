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


def test_queue_cmd_max_runtime_meets_alsched_rule():
    cmd = launch.queue_cmd(CFG, "L-x", "/r")
    assert int(cmd[cmd.index("--max-runtime") + 1]) >= 1.5 * int(cmd[cmd.index("--est") + 1])


def test_no_neural_calls_in_code():
    bad = re.compile(r"tickets\.py|claude\s|\bnotify\b|telegram|api\.telegram|anthropic", re.I)
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
    assert set(routes.values()) <= {"cells", "monthly", "outside", "r1", "r1x", "r1comp", "nodata", "noline", "r1route"}


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


def test_queued_months_block_resubmit_of_same_set(tmp_path):
    """В-7: поставленное в очередь (run.json:new_hm) не считается второй раз до загрузки в Летопись."""
    import json
    run = tmp_path / "L-1"
    run.mkdir()
    (run / "run.json").write_text(json.dumps({"new_hm": {"bbbb": ["2026-02", "2026-03"], "cccc": M3}}), encoding="utf-8")
    q = launch.queued_months({"run_dir": str(tmp_path)})
    assert q["bbbb"] == {"2026-02", "2026-03"} and q["cccc"] == set(M3)
    r = launch.plan(["Г-01"], M3, None, rows=ROWS, have=HAVE, cfg=CFG, queued=q)
    assert r["hm_new"] == 0 and r["hm_queued"] == 5 and "в очереди 5" in launch.summary(r)
    assert launch.queued_months({"run_dir": str(tmp_path / "нет")}) == {}


def test_validate_whitelists():
    import pytest
    ok = launch.hyp_list()[0]["id"]
    launch.validate([ok], ["2026-01"], {})
    for bad in (dict(hyps=["Г-9999"], months=["2026-01"]), dict(hyps=[ok], months=["2026-10"]), dict(hyps=[ok], months=["2026-01; rm -rf /"]),
                dict(hyps=[ok], months=["2026-01"], axes={"нет_оси": ["1"]}), dict(hyps=[1], months=["2026-01"])):
        with pytest.raises(ValueError):
            launch.validate(bad["hyps"], bad["months"], bad.get("axes"))


def _fake_run(tmp_path, with_psim=True):
    import json
    run = tmp_path / "L-1"
    (run / "psim").mkdir(parents=True)
    (run / "run.json").write_text(json.dumps({"id": "L-1", "new_hm": {"bbbb": ["2026-02"], "cccc": ["2026-02"]}}), encoding="utf-8")
    if with_psim:
        (run / "psim" / "psim-p14f1.done").write_text("")
        for cap in (0, 3):   # variant -> {месяц: {cap: [[ts, $], …]}}; bbbb и cccc — одна строка --cells, один вариант
            (run / "psim" / f"closes-p14f1-cap{cap}-2026-02.json").write_text(
                json.dumps({"t-bid-btc4h-q1@chase-pct2-tr1x1-14400-ttl1800": {"2026-02": {str(cap): [[1, 2.5], [2, -1.0]]}}}), encoding="utf-8")
    return run


def test_postload_writes_overlay_and_overlay_counts_as_have(tmp_path):
    import json, postload
    run = _fake_run(tmp_path)
    assert postload.main([str(run)], DOC) == 0
    rows = [json.loads(l) for l in open(run / "overlay.jsonl", encoding="utf-8")]
    assert len(rows) == 2 and {r["cell_id"] for r in rows} == {"bbbb", "cccc"}
    assert rows[0]["trades"] == 2 and rows[0]["pnl_usd"] == 1.5 and rows[0]["ext"]["cell_line"] == LINE
    assert (run / "closes-sum.csv").read_text(encoding="utf-8").startswith("variant,month,cap,n,sum_usd")
    cfg = {"run_dir": str(tmp_path)}
    assert launch.overlay_have(cfg) == {"bbbb": {"2026-02"}, "cccc": {"2026-02"}}
    assert launch.postload_errors(cfg) == []


def test_postload_error_goes_to_log_not_overlay(tmp_path, monkeypatch):
    import postload
    run = _fake_run(tmp_path, with_psim=False)
    assert postload.main([str(run)], DOC) == 1
    assert not (run / "overlay.jsonl").exists()
    assert "psim" in launch.postload_errors({"run_dir": str(tmp_path)})[0]


def _rcfg(tmp_path, **kw):
    (tmp_path / "requests").mkdir()
    (tmp_path / "out").mkdir()
    return dict(CFG, request_dir=str(tmp_path / "requests"), run_dir=str(tmp_path / "out"), alsched=__file__, **kw)


def test_request_submit_writes_request_one_at_a_time(tmp_path):
    """В-7 (Судья): веб-юнит не ставит в очередь, а пишет запрос; второй до обработки первого — busy."""
    import json
    cfg = _rcfg(tmp_path)
    r = launch.request_submit(["Г-01"], M3, None, True, cfg=cfg, p=p(), log=lambda *a: None)
    assert r["ok"] and r["run_id"].startswith("L-")
    req = json.loads((tmp_path / "requests" / (r["run_id"] + ".json")).read_text(encoding="utf-8"))
    assert req["hyps"] == ["Г-01"] and req["months"] == M3
    assert launch.pending_requests(cfg) == [r["run_id"] + ".json"] and not list((tmp_path / "out").iterdir())
    assert launch.request_submit(["Г-01"], M3, None, True, cfg=cfg, p=p(), log=lambda *a: None)["why"] == "busy"
    assert launch.request_submit(["Г-01"], M3, None, False, cfg=cfg, p=p(), log=lambda *a: None)["why"] == "bin-not-confirmed"


def test_process_requests_revalidates_and_always_removes(tmp_path):
    """root-юнит: подделанный запрос (гипотеза вне списка, чужое имя, ссылка) не ставится; запрос удаляется всегда; итог — result.json."""
    import json
    cfg = _rcfg(tmp_path)
    rq = tmp_path / "requests"
    (rq / "L-20261014-200000.json").write_text(json.dumps({"hyps": ["Г-9999"], "months": M3, "axes": {}, "confirm_bin": True}), encoding="utf-8")
    (rq / "evil.json").write_text("{}", encoding="utf-8")
    (rq / "L-20261014-200001.json").write_text("не json", encoding="utf-8")
    (rq / "L-20261014-200002.json").write_text(json.dumps({"hyps": ["Г-10"], "months": ["2026-01"], "axes": {}, "confirm_bin": True}), encoding="utf-8")
    calls = []
    launch.process_requests(cfg, run_cmd=lambda *a, **k: calls.append(a), log=lambda *a: None)
    assert not list(rq.iterdir()) and not calls
    bad = json.loads((tmp_path / "out" / "L-20261014-200000" / "result.json").read_text(encoding="utf-8"))
    assert bad["ok"] is False and bad["why"] == "invalid"
    nothing = json.loads((tmp_path / "out" / "L-20261014-200002" / "result.json").read_text(encoding="utf-8"))
    assert nothing["why"] == "nothing-to-count"
    assert sorted(x.name for x in (tmp_path / "out").iterdir()) == ["L-20261014-200000", "L-20261014-200002"]   # evil.json и не-JSON каталога не получили
    assert launch.request_errors(cfg) == []   # новейший — «нечего считать» не красная строка
    (tmp_path / "out" / "L-20261014-200003").mkdir()
    (tmp_path / "out" / "L-20261014-200003" / "result.json").write_text(json.dumps({"ok": False, "why": "alsched-failed", "log": ["rc=1"]}), encoding="utf-8")
    assert launch.request_errors(cfg) == ["L-20261014-200003: alsched-failed: rc=1"]


def test_process_requests_queues_valid_request_with_same_run_id(tmp_path, monkeypatch):
    import json
    cfg = _rcfg(tmp_path)
    got = {}
    fixed = p()
    monkeypatch.setattr(launch, "plan", lambda *a, **k: fixed)
    monkeypatch.setattr(launch, "validate", lambda *a, **k: None)
    monkeypatch.setattr(launch, "prepare_plan", lambda *a, **k: ({"2026-02": 1}, False))

    class R:
        returncode, stdout, stderr = 0, "", ""
    def run(cmd, **k):
        got["cmd"] = cmd
        return R()
    (tmp_path / "requests" / "L-20261014-210000.json").write_text(json.dumps({"hyps": ["Г-01"], "months": M3, "axes": {}, "confirm_bin": True}), encoding="utf-8")
    launch.process_requests(cfg, run_cmd=run, log=lambda *a: None)
    assert got["cmd"][got["cmd"].index("--name") + 1] == "L-20261014-210000"
    run = tmp_path / "out" / "L-20261014-210000"
    assert json.loads((run / "result.json").read_text(encoding="utf-8"))["ok"] is True and (run / "run.json").exists()
    assert not list((tmp_path / "requests").iterdir())


def test_plan_groups_merges_by_hash_with_own_months_per_hyp():
    g = [{"hyps": ["Г-01"], "months": M3[:2], "axes": {"q": ["5", "10"]}}, {"hyps": ["Г-01"], "months": M3[1:], "axes": {"q": ["10"]}},
         {"hyps": ["Г-02"], "months": M3[:1]}]
    pg = launch.plan_groups(g, rows=ROWS, have=HAVE, cfg=CFG)
    by = {c["hash"]: c for c in pg["cells"]}
    assert by["bbbb"]["new_months"] == ["2026-02", "2026-03"] and by["bbbb"]["have_months"] == ["2026-01"]   # объединение месяцев двух групп
    assert by["aaaa"]["have_months"] == M3[:2] and by["dddd"]["new_months"] == M3[:1]
    assert pg["hm_new"] == 3 and pg["hm_have"] == 3 and pg["hm_grid"] == 6 and pg["months"] == M3
    one = launch.plan_groups([{"hyps": ["Г-01"], "months": M3}], rows=ROWS, have=HAVE, cfg=CFG)
    assert {k: v for k, v in one.items() if k not in ("axis_cov", "cells")} == {k: v for k, v in p().items() if k not in ("axis_cov", "cells")}


def test_coverage_masks_and_validate_groups():
    cv = launch.coverage(CFG, rows=ROWS, have=HAVE, routes={"cccc": "outside"}, queued={"bbbb": {"2026-02"}})
    assert cv["Г-01"]["axes"] == {"q": ["5", "10", "20"]}
    assert cv["Г-01"]["cells"] == [[0, 7, 0, 0], [1, 1, 2, 0], [2, 0, 0, 1]]   # индекс значения, маска посчитано, маска в очереди, вне формы
    import pytest
    launch.validate_groups([{"hyps": [], "months": M3}], CFG, ROWS)
    with pytest.raises(ValueError):
        launch.validate_groups([{"hyps": ["Г-01"], "months": ["1999-01"]}], CFG, ROWS)
    with pytest.raises(ValueError):
        launch.validate_groups({"hyps": []}, CFG, ROWS)


def test_coverage_shared_hash_visible_per_hyp_and_queue_minus_have():
    """TK-278 (проверка Судьи 17.10): общий хеш (Г-27/Г-89/Г-107) виден у каждой гипотезы; очередь не включает уже посчитанное."""
    rows = ROWS + [{"hyp": "Г-02", "hash": "aaaa", "axes": {"q": "7"}}]
    cv = launch.coverage(CFG, rows=rows, have=HAVE, routes={}, queued={"aaaa": set(M3), "bbbb": {"2026-01", "2026-02"}})
    assert cv["Г-02"]["cells"] == [[0, 0, 0, 0], [1, 7, 0, 0]]   # dddd не считали; aaaa (общий) посчитан полностью
    assert [c[-2] for c in cv["Г-01"]["cells"]] == [0, 2, 0]   # aaaa посчитан полностью — не «в очереди»; bbbb: 01 есть, в очереди только 02


def test_letopis_audit_fields():
    """Аудит Летописи 15.10: none без нулей («—»), «н/д» вместо нулевого $, не просеянное не получает вердикт, причина none — из route."""
    d = launch.letopis()
    rows = d["rows"]
    assert d["nhash"] <= len(rows) and rows
    for x in rows:
        if x["status"] == "none":
            assert x["trades"] is None and x["sum"] is None
        assert len(x["nd"]) == len(x["pnl"]) == len(d["months"])
        assert not any(v is not None and n for v, n in zip(x["pnl"], x["nd"]))   # н/д и число не бывают вместе
        if not x["sieved"]:
            assert x["verdict"] == ""
        if x["off"]:
            assert x["status"] == "none" and x["off"] in launch.OFF_LABELS.values()


# --- TK-251 П4: маршруты r1/r1x (R1 в форме) ---
R1DOC = {**DOC, "routes": {"aaaa": "cells", "bbbb": "cells", "cccc": "r1", "dddd": "r1x"},
         "r1spec": {"cccc": ["Г-01", "wall_add_max_lots", "hi", "5", "", ""]}}


def test_r1_routes_are_countable_and_r1comp_is_off():
    assert set(launch.R1_ROUTES) == {"r1", "r1x"} <= set(launch.COUNTABLE) and "r1comp" in launch.OFF_LABELS
    r = launch.plan(["Г-01", "Г-02"], M3, rows=ROWS, have=HAVE, cfg=CFG, routes=R1DOC["routes"])
    by = {c["hash"]: c for c in r["cells"]}
    assert by["cccc"]["new_months"] == M3 and by["dddd"]["new_months"] == M3 and r["hm_off"] == 0
    r = launch.plan(["Г-01"], M3, rows=ROWS, have=HAVE, cfg=CFG, routes={"cccc": "r1comp"})
    assert r["off_by"] == {"r1comp": 3}


def test_prepare_plan_writes_r1spec_and_r1x_apart_from_cells(tmp_path):
    p2 = launch.plan(["Г-01", "Г-02"], M3, rows=ROWS, have=HAVE, cfg=CFG, routes=R1DOC["routes"])
    per, _ = launch.prepare_plan(p2, str(tmp_path), R1DOC)
    assert per == {"2026-01": 0, "2026-02": 1, "2026-03": 1}   # только bbbb (cells); r1/r1x в --cells не попадают
    assert (tmp_path / "L-2026-01.r1spec").read_text(encoding="utf-8").splitlines() == [
        "hash,hyp,col,sign,keep_q,ctx_axis,ctx_q", "cccc,Г-01,wall_add_max_lots,hi,5,,"]
    assert (tmp_path / "L-2026-01.r1x").read_text(encoding="utf-8") == "dddd\n"
    assert (tmp_path / "r1months.txt").read_text(encoding="utf-8").split() == M3
    assert (tmp_path / "months.txt").read_text(encoding="utf-8").split() == ["2026-02", "2026-03"]
    assert "cccc" not in "".join(f.read_text(encoding="utf-8") for f in tmp_path.glob("*.cells"))


def test_prepare_plan_without_r1_is_as_before(tmp_path):
    """Гейт «выкл = прежний»: нет r1/r1x в плане — ни .r1spec, ни .r1x, ни r1months.txt; .cells/.args те же."""
    per, _ = launch.prepare_plan(p(), str(tmp_path), DOC)
    assert not list(tmp_path.glob("*.r1*")) and not (tmp_path / "r1months.txt").exists()
    assert per == {"2026-01": 1, "2026-02": 1, "2026-03": 1}
    assert (tmp_path / "L-2026-02.cells").read_text(encoding="utf-8") == LINE + "\n"


def test_postload_r1_and_engine_in_one_run(tmp_path):
    import json, postload
    run = tmp_path / "L-1"
    (run / "psim").mkdir(parents=True)
    (run / "r1" / "ps").mkdir(parents=True)
    (run / "run.json").write_text(json.dumps({"id": "L-1", "new_hm": {"bbbb": ["2026-02"], "cccc": ["2026-02"], "dddd": ["2026-02"]}}), encoding="utf-8")
    (run / "psim" / "psim-p14f1.done").write_text("")
    (run / "r1" / "psim-r1.done").write_text("")
    for cap in (0, 3):
        (run / "psim" / f"closes-p14f1-cap{cap}-2026-02.json").write_text(
            json.dumps({"t-bid-btc4h-q1@chase-pct2-tr1x1-14400-ttl1800": {"2026-02": {str(cap): [[1, 2.5]]}}}), encoding="utf-8")
        (run / "r1" / "ps" / f"closes-cap{cap}-feb.json").write_text(   # psim R1: ключ = хеш клетки, B1 — база, пропускается; у dddd сделок нет
            json.dumps({"B1": {"2026-02": {str(cap): [[1, 9.0]]}}, "cccc": {"2026-02": {str(cap): [[1, 4.0], [2, -1.5]]}}}), encoding="utf-8")
    doc = {**R1DOC, "lines": {"bbbb": LINE}, "routes": {"bbbb": "cells", "cccc": "r1", "dddd": "r1x"}}
    assert postload.main([str(run)], doc) == 0
    rows = {r["cell_id"]: r for r in map(json.loads, open(run / "overlay.jsonl", encoding="utf-8"))}
    assert set(rows) == {"bbbb", "cccc", "dddd"} and rows["bbbb"]["pnl_usd"] == 2.5 and rows["bbbb"]["ext"]["cell_line"] == LINE
    assert rows["cccc"]["trades"] == 2 and rows["cccc"]["pnl_usd"] == 2.5 and rows["cccc"]["ext"]["route"] == "r1"
    assert rows["dddd"]["trades"] == 0 and rows["dddd"]["ext"]["empty"] and rows["dddd"]["ext"]["route"] == "r1x"
    assert launch.overlay_have({"run_dir": str(tmp_path)})["cccc"] == {"2026-02"}


def test_postload_r1_not_done_is_error(tmp_path):
    import json, postload
    run = tmp_path / "L-1"
    (run / "r1").mkdir(parents=True)
    (run / "run.json").write_text(json.dumps({"id": "L-1", "new_hm": {"cccc": ["2026-02"]}}), encoding="utf-8")
    (run / "r1" / "r1-fail.txt").write_text("psim feb\n", encoding="utf-8")
    assert postload.main([str(run)], R1DOC) == 1 and "psim feb" in (run / "postload.log").read_text(encoding="utf-8")


def test_routes_file_r1spec_matches_plan_and_counts():
    """Охрана TK-249: хеш в r1spec = хеш клетки плана с теми же колонкой и keep_q; r1 — все со знаком, r1x — 270 готовых клеток tk202."""
    import collections
    doc = launch.json.load(open(launch.ROUTES_FILE, encoding="utf-8"))
    rows = {r["hash"]: r for r in launch.plan_rows_cfg(launch.load_config())}   # TK-278: доли F1+F2 (config.fraction [1, 2]) и П-17
    assert set(doc["r1spec"]) == {h for h, r in doc["routes"].items() if r == "r1"}
    for h, (hyp, col, sign, kq, ctx_axis, ctx_q) in doc["r1spec"].items():
        a = rows[h]
        assert (a["hyp"], a["axes"]["col"], str(a["axes"]["keep_q"])) == (hyp, col, kq) and sign in ("hi", "lo") and not ctx_axis and not ctx_q
    cnt = collections.Counter(doc["routes"][r["hash"]] for r in launch.plan_rows("P-14", 1))   # счёт по доле F1: F2 добавляет свои хеши
    assert cnt["r1x"] == 270 and cnt["r1"] == 288 and cnt["r1comp"] == 27 and "r1col" not in cnt


def test_checked_metrics_nd_months_by_n():
    """TK-268: канон без $ (R2A, н/д) сверяется по числу сделок — n closes == n канона точно; $-месяцы — по Σ$ ±0,02."""
    mt = {"n": 30, "by_month": {"2026-01": {"n": 10, "pnl": 5.0}, "2026-02": {"n": 20, "pnl": -1.0}}}
    assert launch.checked_metrics(mt, {"2026-01": (5.01, 10, None, None), "2026-02": (-1.0, 20, None, None)}) is mt
    assert launch.checked_metrics(mt, {"2026-01": (None, 10, None, 1.0), "2026-02": (None, 20, None, 2.0)}) is mt
    assert launch.checked_metrics(mt, {"2026-01": (5.0, 10, None, None), "2026-02": (None, 20, None, 2.0)}) is mt
    assert launch.checked_metrics(mt, {"2026-01": (None, 26, None, 1.0), "2026-02": (None, 52, None, 2.0)}) is None
    assert launch.checked_metrics(mt, {"2026-01": (None, 10, None, 1.0)}) is None   # месяцы closes != месяцы канона
    assert launch.checked_metrics(mt, {"2026-01": (5.0, 10, None, None), "2026-02": (-1.5, 20, None, None)}) is None
    assert launch.checked_metrics(mt, {"2026-01": (None, None, None, None), "2026-02": (None, 20, None, 2.0)}) is None


def test_metrics_why_names_the_reason():
    mt = {"n": 30, "by_month": {"2026-01": {"n": 10, "pnl": 5.0}}}
    assert launch.metrics_why(mt, {"2026-01": (None, 26, None, 1.0)}) == "R2A: канон без $, n канона ≠ n closes (×2,6)"
    assert "расходятся" in launch.metrics_why(mt, {"2026-01": (9.0, 10, None, None)})
    assert "closes нет" in launch.metrics_why(None, {"2026-01": (9.0, 10, None, None)})
    assert launch.metrics_why(None, {}, "нет кодировки") == "нет $ в Летописи (нет кодировки)"




def test_p17_routes_and_bin_guard():
    """TK-279: П-17 рядом с П-14 — все 292 хеша в маршрутах, форма видит клетки обоих протоколов; бинарь без P-17 в bin.protocols отказывает."""
    cfg = launch.load_config()
    assert launch.protocols_of(cfg) == [("P-14", [1, 2]), ("P-15", 1), ("P-17", 1)] and launch.protocols_of({"protocol": "P-14", "fraction": 1}) == [("P-14", 1)]
    p17 = launch.plan_rows("P-17", 1)
    routes = launch.load_routes()
    assert len(p17) == 292 and {r["hash"] for r in p17} <= set(routes) and {r["hash"] for r in launch.plan_rows_cfg(cfg)} >= {r["hash"] for r in p17}
    p = {"cells": [{"hash": p17[0]["hash"], "new_months": ["2026-03"]}]}
    assert launch.bin_protocols_missing(cfg, p) == ["P-17"]
    assert launch.bin_protocols_missing({**cfg, "bin": {**cfg["bin"], "protocols": ["P-14", "P-17"]}}, p) == []
    assert launch.bin_protocols_missing({"protocol": "P-14", "fraction": 1}, p) == []
