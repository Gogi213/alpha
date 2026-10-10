"""dashboard-check.py (TK-003): известный случай Судьи T-21 — плитки читают счёт, «Причины выхода»/гистограмма/
«По монетам» — запасной kpi/trades (сен главный 155/$110,99 против 152/$103,46) → «сломано»; согласованная — «ОК».

Запуск: `python -m pytest tools/compute/tests -q` (на Windows вывод кириллицы — PYTHONIOENCODING=utf-8).
"""
import copy
import importlib.util
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("dashboard_check", os.path.join(HERE, "..", "dashboard-check.py"))
dc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dc)

# две сделки по $500: +100 bps (трейл, +$5) и −40 bps (стоп, −$2)
TRADES = [["2026-09-01", "NEARUSDT", 1, 2, 100.0, 0, "trail", 1.0, 500.0],
          ["2026-09-02", "ADAUSDT", 3, 4, -40.0, 0, "stop", 1.0, 500.0]]
GOOD = {
    "generated_utc": "2026-09-27 00:00", "position_usd": 500.0, "deposit_usd": 2500.0,
    "trades": {"sep": {"main": TRADES}},
    "kpi": {"sep": {"main": {"n": 2, "net_usd": 3.0, "daily_usd": {"2026-09-01": 5.0, "2026-09-02": -2.0},
                             "reasons": {"trail": {"n": 1, "usd": 5.0}, "stop": {"n": 1, "usd": -2.0}}}}},
    "account": {"sep": {"main": {"n": 2, "net_usd": 3.0, "net_pct": 0.12,
                                 "daily_usd": {"2026-09-01": 5.0, "2026-09-02": -2.0}}}},
}


def page(tmp_path, D, name="page.html"):
    p = tmp_path / name
    p.write_text('<html><script id="data" type="application/json">' + json.dumps(D).replace("</", "<\\/") +
                 "</script></html>", encoding="utf-8")
    return str(p)


def test_consistent_page_ok(tmp_path):
    assert dc.run(page(tmp_path, GOOD))["level"] == dc.OK


def test_known_case_account_vs_fallback_is_broken(tmp_path):
    D = copy.deepcopy(GOOD)
    # счёт взял на сделку больше, чем в запасном списке (как сен главный 155 против 152)
    D["account"]["sep"]["main"].update(n=3, net_usd=4.0, net_pct=0.16, daily_usd={"2026-09-01": 6.0, "2026-09-02": -2.0})
    res = dc.run(page(tmp_path, D))
    assert res["level"] == dc.BROKEN
    what = " | ".join(i["what"] for i in res["items"])
    assert "«По монетам» 2" in what and "«Причины выхода» 2" in what


def test_source_mismatch_and_stale_publication_broken(tmp_path):
    src = tmp_path / "src.json"
    src.write_text(json.dumps({"deposit_usd": 5000.0}), encoding="utf-8")
    assert dc.run(page(tmp_path, GOOD), sources=[str(src)])["level"] == dc.BROKEN
    old = copy.deepcopy(GOOD)
    old["generated_utc"] = "2026-09-26 00:00"
    res = dc.run(page(tmp_path, GOOD), published=page(tmp_path, old, "pub.html"))
    assert res["level"] == dc.BROKEN and any(i["where"] == "публикация" for i in res["items"])


def test_rounding_of_days_is_not_a_finding(tmp_path):
    D = copy.deepcopy(GOOD)
    D["account"]["sep"]["main"]["daily_usd"] = {"2026-09-01": 5.004, "2026-09-02": -1.996}  # Σ 3.008: округление
    assert dc.run(page(tmp_path, D))["level"] == dc.OK
