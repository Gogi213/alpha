r"""t21-snapshot.py: снимок «до» для T-21 (docs/research/reviews/t21-canon-2026-09-27.md §3).

Проверяем: (1) `load_dashboard_json` достаёт встроенный JSON из HTML с экранированием `<\/`
(так Python/JS обычно экранируют закрывающий тег внутри `<script>`); (2) `Recorder.add` не создаёт
задвоенных `id` — при совпадении ключа второй записи присваивается суффикс `#2`, `#3`, ...; (3) сквозной
прогон `extract_dashboard` на маленьком фейковом дереве даёт непустой, уникальный по `id` набор записей
с ожидаемыми `formula_tags`.

Запуск: `python -m pytest tools/compute/tests/test_t21_snapshot.py -q`
(на Windows вывод кириллицы — `PYTHONIOENCODING=utf-8`).
"""
import importlib.util
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE = os.path.join(HERE, "..", "t21-snapshot.py")


def load():
    spec = importlib.util.spec_from_file_location("t21_snapshot", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ts = load()


def test_load_dashboard_json_handles_escaped_close_tag(tmp_path):
    payload = {"a": 1, "b": {"c": [1, 2, 3]}, "s": "text with <\\/script> inside"}
    raw = json.dumps(payload, ensure_ascii=False)
    # так объект попадает в страницу: '</' экранируется, чтобы не закрыть <script> раньше времени
    escaped = raw.replace("</", "<\\/")
    html = (
        "<html><body>\n"
        f'<script id="data" type="application/json">{escaped}</script>\n'
        "</body></html>"
    )
    p = tmp_path / "fake.html"
    p.write_text(html, encoding="utf-8")

    data = ts.load_dashboard_json(p)

    assert data == payload
    assert data["b"]["c"] == [1, 2, 3]


def test_recorder_add_ignores_none_and_containers():
    rec = ts.Recorder()
    rec.add("src", "prod", "m", "aug", "v", None, [])
    rec.add("src", "prod", "m2", "aug", "v", {"nested": 1}, [])
    rec.add("src", "prod", "m3", "aug", "v", [1, 2], [])
    assert rec.records == []


def test_recorder_add_dedupes_ids_with_suffix():
    rec = ts.Recorder()
    rec.add("src", "prod", "metric", "aug", "v1", 1.0, ["dollars:Ф1"])
    rec.add("src", "prod", "metric", "aug", "v1", 2.0, ["dollars:Ф1"])
    rec.add("src", "prod", "metric", "aug", "v1", 3.0, ["dollars:Ф1"])

    ids = [r["id"] for r in rec.records]
    assert len(ids) == len(set(ids)) == 3
    assert ids[0].endswith("::v1")
    assert ids[1].endswith("#2")
    assert ids[2].endswith("#3")
    # значения не потеряны при переименовании id
    assert [r["value"] for r in rec.records] == [1.0, 2.0, 3.0]


def test_flatten_scalars_skips_skip_keys_and_lists():
    rec = ts.Recorder()
    obj = {
        "n": 10,
        "net_usd": 5.5,
        "daily_usd": {"2026-09-01": 1.0},  # в SKIP_KEYS — не должен попасть как отдельная запись
        "reasons": {"take": {"n": 1}},  # список? нет, это dict внутри dict — flatten_scalars рекурсирует
        "periods": [1, 2, 3],  # в SKIP_KEYS
    }
    ts.flatten_scalars("account", obj, rec, "src", "prod", "aug", "v1", ["dollars:Ф1"])
    metrics = {r["metric"] for r in rec.records}
    assert "account.n" in metrics
    assert "account.net_usd" in metrics
    assert not any(m.startswith("account.daily_usd") for m in metrics)
    assert not any(m.startswith("account.periods") for m in metrics)
    # reasons.take.n дошёл рекурсией, т.к. reasons — не в SKIP_KEYS
    assert "account.reasons.take.n" in metrics


def test_extract_dashboard_end_to_end_unique_ids_and_tags(tmp_path):
    fake_data = {
        "account": {
            "sep": {
                "v1": {"n": 10, "net_usd": 42.0, "dd_usd": 5.0, "dd_pct": 1.0,
                       "daily_usd": {"2026-09-01": 1.0}},
            }
        },
        "kpi": {
            "sep": {
                "v1": {"n": 10, "net_usd": 40.0, "reasons": {"take": {"n": 5, "usd": 20.0, "win": 4}}},
            }
        },
        "protections": {
            "sep": {
                "v1": [
                    {"label": "Без защит", "n": 10, "net_usd": 42.0},
                    {"label": "+ потолок 3", "n": 8, "net_usd": 30.0},
                ]
            }
        },
        "nh": {
            "n_rows": 5, "n_pass": 2, "status": "ok",
            "by_variant": {
                "v1": {
                    "aug": {"n": 3, "median": 1.0, "periods": [1, 2, 3], "roll_hist": [1]},
                    "sep": {"n": 4, "median": 2.0},
                    "kpi07": {"frac": {"aug": 0.4, "sep": 0.1}, "verdict": "не проходит"},
                }
            },
            "rating": [{"name": "v1", "key": "v1", "place": 1, "aug": {"usd": 1.0}, "sep": {"usd": 2.0}}],
            "t32": {"grid": [{"hedge": "нет", "limit": "нет", "aug": {"usd": 1.0, "n": 1}}]},
            "lev": {"deposit_usd": 2500.0, "rows": {"aug": {"1": {"usd": 1.0}}}},
            "approach": {"max_approach_number": 1, "tail_from": 1, "n_trades": {"aug": 1},
                         "by_number": [{"key": "1", "aug": {"n": 1, "usd": 1.0}}]},
            "edges_days": [0, 1],
        },
        "p05": {"status": "draft", "cells": {"c1": {"part": "a1", "months": {"aug": {"usd": 1.0}}}},
                "account": {}, "size_dd": {}},
        "p02": {"r2_status": "draft", "coins_status": "draft",
                "rows": [{"h": "Г-1", "unit": "bps", "months": {"август": {"est": 1.0}}}]},
    }
    raw = json.dumps(fake_data, ensure_ascii=False)
    escaped = raw.replace("</", "<\\/")
    html = f'<script id="data" type="application/json">{escaped}</script>'
    p = tmp_path / "index-fake.html"
    p.write_text(html, encoding="utf-8")

    data = ts.load_dashboard_json(p)
    rec = ts.Recorder()
    missing = []
    ts.extract_dashboard(data, rec, "index-fake.html", missing)

    assert rec.records, "extract_dashboard не дал ни одной записи на фейковом дереве"
    ids = [r["id"] for r in rec.records]
    assert len(ids) == len(set(ids))

    by_metric = {(r["metric"], r["period"], r["variant"]): r for r in rec.records}
    account_rec = by_metric[("account.net_usd", "sep", "v1")]
    assert account_rec["value"] == 42.0
    assert "portfolio:one-per-coin" in account_rec["formula_tags"]
    assert "drawdown:П1" in account_rec["formula_tags"]

    kpi_rec = by_metric[("kpi_fallback.net_usd", "sep", "v1")]
    assert "portfolio:none" in kpi_rec["formula_tags"]

    kpi07_rec = by_metric[("nh.kpi07.frac", "aug", "v1")]
    assert kpi07_rec["value"] == 0.4

    p02_rec = by_metric[("p02.rows.months.est", "aug", "Г-1")]
    assert p02_rec["value"] == 1.0
    assert "day:t0" in p02_rec["formula_tags"]


def test_norm_source_forward_slashes_relative():
    """Судья 09b31f6, условие 3: id «до» (Windows) и «после» (дека) сходятся — пути одним «/», от корня репо."""
    root = str(ts.ROOT)
    bs = chr(92)
    assert ts.norm_source(root + bs + bs.join(["data", "kpi", "x.json"])) == "data/kpi/x.json"
    assert ts.norm_source(bs.join(["data", "t32", "grid.json"])) == "data/t32/grid.json"
    assert ts.norm_source("/home/deck/alpha/data/x.json") == "/home/deck/alpha/data/x.json"
