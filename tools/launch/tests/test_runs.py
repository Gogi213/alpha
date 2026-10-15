import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))
import runs

PS = """ядра: занято 15/16 по заявкам; в очереди 1
1015014502 running prod    ядер=12 L-20261015-014502     252.0 мин rc= env BIN=/x bash run-launch.sh
1015022210 queued  prod    ядер=12 L-20261015-022210       0.0 мин rc= env BIN=/x bash run-launch.sh
1014170116 running gate    ядер= 3 tk231-psim              38.0 мин rc= bash psim.sh
"""
NOW = 1_800_000_000.0


def mk(tmp, name, hyps=("Г-27",), new_hm=None, days=0, total=0, psim=(), claim_ago=None, **files):
    d = tmp / name
    d.mkdir()
    (d / "run.json").write_text(json.dumps({"hyps": list(hyps), "hm_new": 12, "new_hm": new_hm or {"h1": ["2026-01", "2026-02"]}}), encoding="utf-8")
    if total:
        (d / "days.txt").write_text("".join(f"jan {i}\n" for i in range(total)), encoding="utf-8")
    for i in range(days):
        x = d / "out" / f"run-jan-{i}"
        x.mkdir(parents=True)
        (x / "claim").mkdir()
        (x / "done").write_text("")
        if claim_ago is not None:
            os.utime(x / "claim", (NOW - claim_ago, NOW - claim_ago))
    for m in psim:
        (d / "psim").mkdir(exist_ok=True)
        for c in (0, 3):
            (d / "psim" / f"closes-p14f1-cap{c}-{m}.json").write_text("{}")
    for fn, txt in files.items():
        (d / fn.replace("__", ".")).parent.mkdir(parents=True, exist_ok=True)
        (d / fn.replace("__", ".")).write_text(txt, encoding="utf-8")
    return str(d)


def test_parse_ps_and_foreign():
    ps = runs.parse_ps(PS)
    assert (ps["busy"], ps["total"], len(ps["jobs"])) == (15, 16, 3)
    assert [j["name"] for j in runs.foreign_jobs(ps, {"L-20261015-014502", "L-20261015-022210"})] == ["tk231-psim"]


def test_weights_from_f1_pass():
    assert abs(runs.W_DAYS + runs.W_PSIM - 1) < 1e-12 and abs(runs.W_DAYS - 690 / 740) < 1e-12
    assert runs.percent((251, 251), (0, 9), False) == 93 and runs.percent((251, 251), (9, 9), False) == 100 and runs.percent((0, 251), (0, 9), True) == 100


def test_five_statuses(tmp_path):
    ps = runs.parse_ps(PS)
    job = lambda n: next(j for j in ps["jobs"] if j["name"] == n)
    r = runs.run_record(mk(tmp_path, "L-20261015-014502", days=100, total=200, claim_ago=3600), "L-20261015-014502", NOW, job("L-20261015-014502"), est_s=45000)
    assert r["s"] == "run" and r["days"] == [100, 200] and r["src"] == "по темпу готовых суток" and r["cores"] == 12
    assert r["left_min"] == 110 and 0 < r["pct"] < 93         # 100 суток за 60 мин = 0,6 мин/сутки × 100 осталось + psim 50 мин
    q = runs.run_record(str(tmp_path / "нет"), "L-20261015-022210", NOW, job("L-20261015-022210"), request={"hyps": ["Г-25"]}, est_s=45000)
    assert q["s"] == "queue" and q["hyps"] == ["Г-25"] and q["src"].startswith("оценка заявки") and q["left_min"] == 750 and q["pct"] == 0
    full = dict(days=3, total=3, psim=("2026-01", "2026-02"), overlay__jsonl="{}\n", psim__x="")
    d = mk(tmp_path, "L-w", **{k: v for k, v in full.items() if k not in ("psim__x",)})
    (tmp_path / "L-w" / "psim" / "psim-p14f1.done").write_text("")
    assert runs.run_record(d, "L-w", NOW, in_canon=False)["s"] == "wait" and runs.run_record(d, "L-w", NOW, in_canon=True)["s"] == "done"
    f = runs.run_record(mk(tmp_path, "L-f", result__json=json.dumps({"ok": False, "why": "потолок 501 хеша"})), "L-f", NOW)
    assert f["s"] == "fail" and f["why"] == "потолок 501 хеша"
    lost = runs.run_record(mk(tmp_path, "L-lost", days=5, total=10), "L-lost", NOW)       # run.json есть, в ps нет, итога нет
    assert lost["s"] == "fail" and "оборвано" in lost["why"]
    red = runs.run_record(mk(tmp_path, "L-red", days=3, total=3, postload__log="L-red: нет closes"), "L-red", NOW)
    assert red["s"] == "fail" and "нет closes" in red["why"]


def test_eta_before_first_day_uses_request_estimate():
    left, src = runs.eta(NOW, (0, 251), (0, 9), None, 45000, 30.0)
    assert (left, src) == (720, "оценка заявки (назначено, не измерено)")


def test_run_metrics_and_view(tmp_path):
    import launch
    a = 1769904000000   # 2026-02-01 UTC
    h = "0123456789abcdef"
    d = mk(tmp_path, "L-20261015-000001", new_hm={h: ["2026-02"]},
           overlay__jsonl=json.dumps({"cell_id": h, "month": "2026-02", "ext": {"cell_line": "t-form state-x"}}) + "\n")
    (tmp_path / "L-20261015-000001" / "psim").mkdir()
    (tmp_path / "L-20261015-000001" / "psim" / "psim-p14f1.done").write_text("")
    tr = [[a + 3600000, 10.0, "BTC"], [a + 86400000 + 3600000, -4.0, "ETH"]]
    (tmp_path / "L-20261015-000001" / "psim" / "closes-p14f1-cap0-2026-02.json").write_text(json.dumps({"state-x@t-form": {"2026-02": {"0": tr}}}))
    m = runs.run_metrics(d)[h]
    assert (m["n"], m["pnl"], m["winrate"]) == (2, 6.0, 0.5)
    cfg = launch.load_config() | {"run_dir": str(tmp_path), "request_dir": str(tmp_path / "req")}
    v = launch.runs_view(cfg, PS, NOW)
    r = next(x for x in v["runs"] if x["id"] == "L-20261015-000001")
    assert r["s"] == "wait" and r["pct"] == 100 and r["what"][0]["hm"] == 1 and v["cores"] == {"busy": 15, "total": 16}
    assert [f["name"] for f in v["foreign"]] == ["L-20261015-014502", "L-20261015-022210", "tk231-psim"]   # прогонов этих в run_dir нет — для страницы они «чужие»
