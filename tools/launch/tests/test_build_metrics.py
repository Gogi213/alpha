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


def test_direct_r1_closes(tmp_path):
    """TK-258: --direct — ключ файла = хеш (R1), B1 пропущен, месяц без хеша = 0 сделок, хеш из ИСТОЧНИКОВ не перетирается."""
    routes = {"lines": {"hR": "f1 s1"}, "monthly": {}}
    json.dump(routes, open(tmp_path / "routes.json", "w"))
    json.dump({"s1@f1": {"2026-02": {"0": [[A + H, 1.0, "BTC"], [A + D + H, 2.0, "ETH"]]}}}, open(tmp_path / "c-2026-02.json", "w"))
    r1 = {"B1": {"2026-02": {"0": [[A, 99.0]]}},
          "hR": {"2026-02": {"0": [[A, 50.0]]}},                                         # уже есть в ИСТОЧНИКАХ — берётся оттуда
          "hX": {"2026-02": {"0": [[A + H, 5.0], [A + D + H, -2.0]], "3": [[A, 7.0]]}}}   # cap 3 не берётся
    json.dump(r1, open(tmp_path / "closes-cap0-feb.json", "w"))
    json.dump({"hY": {"2026-03": {"0": [[A + 30 * D, 4.0]]}}}, open(tmp_path / "closes-cap0-mar.json", "w"))   # hX в марте отсутствует
    out = tmp_path / "m.jsonl"
    build_metrics.main(["--out", str(out), "--routes", str(tmp_path / "routes.json"), "--months", "2026-02,2026-03",
                        "--direct", str(tmp_path / "closes-cap0-{mon}.json"), str(tmp_path / "c-{m}.json")])
    rows = {json.loads(x)["hash"]: json.loads(x) for x in open(out)}
    assert sorted(rows) == ["hR", "hX", "hY"]
    assert rows["hR"]["n"] == 2 and rows["hR"]["pnl"] == 3.0
    assert rows["hX"]["n"] == 2 and rows["hX"]["pnl"] == 3.0 and rows["hX"]["by_month"] == {"2026-02": {"n": 2, "pnl": 3.0}, "2026-03": {"n": 0, "pnl": 0.0}}
    assert rows["hY"]["by_month"]["2026-02"] == {"n": 0, "pnl": 0.0} and rows["hY"]["n"] == 1
    assert launch.checked_metrics(rows["hX"], {"2026-02": (3.0, 2, None), "2026-03": (0.0, 0, None)}) is rows["hX"]


def test_direct_alias_cells(tmp_path):
    """TK-268: --alias-cells — ключ файла = имя клетки (R1 TK-182), хеш = id по cells.jsonl (set_name p12-…, имя однозначно)."""
    json.dump({"lines": {}, "monthly": {}}, open(tmp_path / "routes.json", "w"))
    cells = [{"id": "0123456789abcdef", "form": "g04-k20", "set_name": "p12-r1/g04-k20"},
             {"id": "aaaaaaaaaaaa0001", "form": "dup", "set_name": "p12-r1/dup"}, {"id": "bbbbbbbbbbbb0002", "form": "dup", "set_name": "p12-r1/dup"},
             {"id": "cccccccccccc0003", "form": "other", "set_name": "f1/other"}]
    with open(tmp_path / "cells.jsonl", "w") as f:
        f.write("".join(json.dumps(c) + "\n" for c in cells))
    assert build_metrics.cell_alias(str(tmp_path / "cells.jsonl")) == {"g04-k20": "0123456789abcdef"}
    json.dump({"B1": {"2026-02": {"0": [[A, 9.0]]}}, "g04-k20": {"2026-02": {"0": [[A + H, 5.0]]}}, "dup": {"2026-02": {"0": [[A, 1.0]]}}},
              open(tmp_path / "c-2026-02.json", "w"))
    out = tmp_path / "m.jsonl"
    build_metrics.main(["--out", str(out), "--routes", str(tmp_path / "routes.json"), "--months", "2026-02", "--alias-cells", str(tmp_path / "cells.jsonl"),
                        "--direct", str(tmp_path / "c-{m}.json")])
    assert sorted(json.loads(x)["hash"] for x in open(out)) == ["0123456789abcdef", "dup"]   # неоднозначное имя остаётся как есть


def test_zero_fill(tmp_path):
    """TK-268: --zero-fill — месяц с файлом источника, но без варианта = 0 сделок; месяц без файла не заполняется; без флага месяца нет."""
    routes = {"lines": {"h1": "f1 s1"}, "monthly": {}}
    json.dump(routes, open(tmp_path / "routes.json", "w"))
    json.dump({"s1@f1": {"2026-02": {"0": [[A + H, 3.0, "BTC"]]}}}, open(tmp_path / "c-2026-02.json", "w"))
    json.dump({"other": {}}, open(tmp_path / "c-2026-03.json", "w"))   # файл марта есть, варианта нет
    base = ["--routes", str(tmp_path / "routes.json"), "--months", "2026-02,2026-03,2026-04", str(tmp_path / "c-{m}.json")]
    out = tmp_path / "m.jsonl"
    build_metrics.main(["--out", str(out)] + base)
    assert list(json.loads(open(out).readline())["by_month"]) == ["2026-02"]
    build_metrics.main(["--out", str(out), "--zero-fill"] + base)
    bm = json.loads(open(out).readline())["by_month"]
    assert bm == {"2026-02": {"n": 1, "pnl": 3.0}, "2026-03": {"n": 0, "pnl": 0.0}}   # апреля без файла нет
