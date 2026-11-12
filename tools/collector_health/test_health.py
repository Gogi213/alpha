import json
import sys
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import serve  # noqa: E402

NOW = 1_762_885_000_000
EXPECTED = [{"id": "binance", "name": "Binance"}, {"id": "bybit", "name": "Bybit"}]
THR = {"stale_s": 30}


def doc(**kw):
    d = {
        "schema": 1, "exchange": "binance", "ts_ms": NOW - 3000, "started_ms": NOW - 10**6,
        "symbols": {"subscribed": 412, "with_data_1m": 412},
        "ws": {"connections": 16, "ping_ms": {"p50": 38, "max": 71},
               "timeouts_1h": 0, "timeouts_24h": 0, "reconnects_1h": 0, "reconnects_24h": 2},
        "lag_in_ms": {"p50": 45, "p99": 310, "window_s": 60},
        "lag_out_ms": {"p50": 6, "p99": 22, "window_s": 60},
        "last_frame_ms": NOW - 3000, "last_write_ms": NOW - 3000,
    }
    d.update(kw)
    return d


def put(tmp, name, body):
    (tmp / f"{name}.json").write_text(body if isinstance(body, str) else json.dumps(body), encoding="utf-8")


def by_ex(rows):
    return {r["exchange"]: r for r in rows}


def test_parse_fresh_file(tmp_path):
    put(tmp_path, "binance", doc())
    r = by_ex(serve.collect(tmp_path, NOW, EXPECTED, THR))["binance"]
    assert r["state"] == "ok" and r["age_s"] == 3 and r["name"] == "Binance"
    assert r["lag_in_ms"]["p99"] == 310 and r["symbols"]["with_data_1m"] == 412


def test_empty_dir_expected_rows_off(tmp_path):
    rows = serve.collect(tmp_path, NOW, EXPECTED, None)
    assert [r["exchange"] for r in rows] == ["binance", "bybit"]
    assert all(r["state"] == "off" for r in rows)


def test_broken_json(tmp_path):
    put(tmp_path, "binance", "{not json")
    r = by_ex(serve.collect(tmp_path, NOW, EXPECTED, None))["binance"]
    assert r["state"] == "bad" and "не читается" in r["note"]


def test_missing_fields_and_wrong_schema(tmp_path):
    put(tmp_path, "binance", {"schema": 1, "ts_ms": NOW})
    put(tmp_path, "bybit", doc(schema=2))
    rows = by_ex(serve.collect(tmp_path, NOW, EXPECTED, None))
    assert rows["binance"]["state"] == "bad" and rows["bybit"]["state"] == "bad"


def test_stale_file(tmp_path):
    put(tmp_path, "binance", doc(ts_ms=NOW - 31_000))
    put(tmp_path, "bybit", doc(ts_ms=NOW - 30_000))
    rows = by_ex(serve.collect(tmp_path, NOW, EXPECTED, THR))
    assert rows["binance"]["state"] == "bad" and rows["binance"]["age_s"] == 31
    assert rows["binance"]["note"] == "файл не обновлялся 31 с"
    assert rows["bybit"]["state"] == "ok"


def test_stale_file_without_thresholds_not_fresh(tmp_path):
    put(tmp_path, "binance", doc(ts_ms=NOW - 600_000))
    r = by_ex(serve.collect(tmp_path, NOW, EXPECTED, None))["binance"]
    assert r["state"] == "warn" and "свежий" not in r["note"]


def test_states(tmp_path):
    d = doc()
    d["ws"]["timeouts_1h"] = 1
    put(tmp_path, "binance", d)
    put(tmp_path, "bybit", doc(symbols={"subscribed": 10, "with_data_1m": 9}))
    rows = by_ex(serve.collect(tmp_path, NOW, EXPECTED, THR))
    assert rows["binance"]["state"] == "warn" and rows["bybit"]["state"] == "warn"


def test_symbols_508_of_509_warn_0_of_509_bad(tmp_path):
    put(tmp_path, "binance", doc(symbols={"subscribed": 509, "with_data_1m": 508}))
    put(tmp_path, "bybit", doc(symbols={"subscribed": 509, "with_data_1m": 0}))
    rows = by_ex(serve.collect(tmp_path, NOW, EXPECTED, THR))
    assert rows["binance"]["state"] == "warn" and rows["bybit"]["state"] == "bad"


def test_new_exchange_without_code_change(tmp_path):
    put(tmp_path, "okx", doc(exchange="okx"))
    rows = serve.collect(tmp_path, NOW, EXPECTED, THR)
    assert [r["exchange"] for r in rows] == ["binance", "bybit", "okx"]
    assert rows[2]["name"] == "okx" and rows[2]["state"] == "ok"


def test_foreign_kind_skipped(tmp_path):
    put(tmp_path, "upload-binance", {"schema": 1, "kind": "upload", "exchange": "binance", "ts_ms": NOW})
    rows = serve.collect(tmp_path, NOW, EXPECTED, None)
    assert [r["exchange"] for r in rows] == ["binance", "bybit"]


def test_null_lag_passes_through(tmp_path):
    put(tmp_path, "binance", doc(lag_in_ms={"p50": None, "p99": None, "window_s": 60}))
    r = by_ex(serve.collect(tmp_path, NOW, EXPECTED, THR))["binance"]
    assert r["state"] == "ok" and r["lag_in_ms"]["p50"] is None


def test_http_api_and_page(tmp_path):
    put(tmp_path, "binance", doc(ts_ms=int(__import__("time").time() * 1000)))

    class H(serve.Handler):
        health_dir = tmp_path
        expected = EXPECTED
        thresholds = THR

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        api = json.load(urllib.request.urlopen(base + "/api/health"))
        assert [r["exchange"] for r in api["rows"]] == ["binance", "bybit"]
        assert api["rows"][0]["state"] == "ok" and api["rows"][1]["state"] == "off"
        assert "Здоровье коллекторов" in urllib.request.urlopen(base + "/").read().decode()
    finally:
        srv.shutdown()


FRESH_OK = {"late_ms": 2000, "kinds": {"book": {"late_1h": 0, "late_24h": 0, "max_1h_ms": 310, "max_24h_ms": 420}, "trade": {"late_1h": 0, "late_24h": 3, "max_1h_ms": 150, "max_24h_ms": 6400}}}


def test_fresh_field_passes_through_and_late_records_in_hour_are_warn(tmp_path):
    put(tmp_path, "binance", doc(fresh=FRESH_OK))
    bad = {"late_ms": 2000, "kinds": {"trade": {"late_1h": 5, "late_24h": 9, "max_1h_ms": 4800, "max_24h_ms": 6400}}}
    put(tmp_path, "bybit", doc(fresh=bad))
    rows = by_ex(serve.collect(tmp_path, NOW, EXPECTED, THR))
    assert rows["binance"]["state"] == "ok" and rows["binance"]["fresh"]["kinds"]["trade"]["max_24h_ms"] == 6400  # за сутки были — за час нет: не жёлтая
    assert rows["bybit"]["state"] == "warn" and "записей старше 2 с: 5" in rows["bybit"]["note"]


def test_row_without_fresh_field_is_ok_and_empty(tmp_path):
    put(tmp_path, "binance", doc())  # health старого ядра (Binance до перевода) — поля нет
    r = by_ex(serve.collect(tmp_path, NOW, EXPECTED, THR))["binance"]
    assert r["state"] == "ok" and r["fresh"] is None


def test_entrance_switch_in_hour_is_warn_and_passes_through(tmp_path):
    """G-022: линия перешла на запасной вход за час — строка жёлтая; за сутки были, за час нет — зелёная; поле старого health.json отсутствует — не ошибка. Столбец есть на странице."""
    ws = dict(doc()["ws"], entrance_switches_1h=2, entrance_switches_24h=3)
    put(tmp_path, "binance", doc(ws=ws))
    put(tmp_path, "bybit", doc(ws=dict(ws, entrance_switches_1h=0)))
    rows = by_ex(serve.collect(tmp_path, NOW, EXPECTED, THR))
    assert rows["binance"]["state"] == "warn" and "запасной вход: 2" in rows["binance"]["note"] and rows["binance"]["ws"]["entrance_switches_24h"] == 3
    assert rows["bybit"]["state"] == "ok"
    put(tmp_path, "bybit", doc())
    assert by_ex(serve.collect(tmp_path, NOW, EXPECTED, THR))["bybit"]["state"] == "ok"
    page = (Path(serve.__file__).resolve().parent / "page.html").read_text(encoding="utf-8")
    assert "Смена входа" in page and "entrance_switches_1h" in page and page.count('colspan="9"') == 2


def test_hour_acceptance_file_in_health_dir_is_not_a_row(tmp_path):
    """accept-<биржа>-<UTC>.json (kind=accept_hour) лежит в /data/health рядом с health.json — строкой страницы не становится."""
    put(tmp_path, "binance", doc())
    put(tmp_path, "accept-binance-20261112T092100Z", {"schema": 1, "kind": "accept_hour", "exchange": "binance", "ts_ms": NOW, "verdict": "ok"})
    rows = serve.collect(tmp_path, NOW, EXPECTED, THR)
    assert [r["exchange"] for r in rows] == ["binance", "bybit"]
