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
    r = by_ex(serve.collect(tmp_path, NOW, EXPECTED, None))["binance"]
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


def test_stale_ts_needs_threshold(tmp_path):
    put(tmp_path, "binance", doc(ts_ms=NOW - 600_000))
    assert by_ex(serve.collect(tmp_path, NOW, EXPECTED, None))["binance"]["state"] == "ok"
    r = by_ex(serve.collect(tmp_path, NOW, EXPECTED, {"stale_s": 60}))["binance"]
    assert r["state"] == "bad" and r["age_s"] == 600


def test_states(tmp_path):
    d = doc()
    d["ws"]["timeouts_1h"] = 1
    put(tmp_path, "binance", d)
    put(tmp_path, "bybit", doc(symbols={"subscribed": 10, "with_data_1m": 9}))
    rows = by_ex(serve.collect(tmp_path, NOW, EXPECTED, None))
    assert rows["binance"]["state"] == "warn" and rows["bybit"]["state"] == "bad"


def test_new_exchange_without_code_change(tmp_path):
    put(tmp_path, "okx", doc(exchange="okx"))
    rows = serve.collect(tmp_path, NOW, EXPECTED, None)
    assert [r["exchange"] for r in rows] == ["binance", "bybit", "okx"]
    assert rows[2]["name"] == "okx" and rows[2]["state"] == "ok"


def test_foreign_kind_skipped(tmp_path):
    put(tmp_path, "upload-binance", {"schema": 1, "kind": "upload", "exchange": "binance", "ts_ms": NOW})
    rows = serve.collect(tmp_path, NOW, EXPECTED, None)
    assert [r["exchange"] for r in rows] == ["binance", "bybit"]


def test_null_lag_passes_through(tmp_path):
    put(tmp_path, "binance", doc(lag_in_ms={"p50": None, "p99": None, "window_s": 60}))
    r = by_ex(serve.collect(tmp_path, NOW, EXPECTED, None))["binance"]
    assert r["state"] == "ok" and r["lag_in_ms"]["p50"] is None


def test_http_api_and_page(tmp_path):
    put(tmp_path, "binance", doc(ts_ms=int(__import__("time").time() * 1000)))

    class H(serve.Handler):
        health_dir = tmp_path
        expected = EXPECTED
        thresholds = None

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
