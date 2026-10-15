import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))
import build_metrics, launch, metrics

A = metrics.month_bounds("2026-02")[0]
D, H = metrics.MS_DAY, 3_600_000


def test_build_and_check(tmp_path):
    routes = {"lines": {"h1": "f1 s1", "h2": "f2 s2"}, "monthly": {}}
    json.dump(routes, open(tmp_path / "routes.json", "w"))
    closes = {"s1@f1": {"2026-02": {"0": [[A + H, 10.0, "BTC"], [A + D + H, -4.0, "ETH"]]}}}   # h2 в источнике нет
    json.dump(closes, open(tmp_path / "c-2026-02.json", "w"))
    out = tmp_path / "m.jsonl"
    build_metrics.main(["--out", str(out), "--routes", str(tmp_path / "routes.json"), "--months", "2026-02", str(tmp_path / "c-{m}.json")])
    rows = [json.loads(x) for x in open(out)]
    assert [r["hash"] for r in rows] == ["h1"] and rows[0]["n"] == 2 and rows[0]["pnl"] == 6.0 and rows[0]["by_month"]["2026-02"] == {"n": 2, "pnl": 6.0}
    mt = rows[0]
    assert launch.checked_metrics(mt, {"2026-02": (6.0, 2, None)}) is mt
    assert launch.checked_metrics(mt, {"2026-02": (6.01, 2, None)}) is mt          # в допуске 0,02
    assert launch.checked_metrics(mt, {"2026-02": (7.0, 2, None)}) is None          # Σ$ не сходится с каноном
    assert launch.checked_metrics(mt, {"2026-02": (6.0, 2, None), "2026-03": (1.0, 1, None)}) is None   # месяц канона без closes
    assert launch.checked_metrics(None, {}) is None
