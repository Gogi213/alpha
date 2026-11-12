import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import accept_hour as ah  # noqa: E402
from wscore import health  # noqa: E402
from wscore.writer import DayWriter  # noqa: E402

POOL = ["AVAXUSDT", "ETHUSDT"]


def book(sym, E, u):
    return ("b", sym, E, E - 1, u, "8.6", "4", "8.7", "2")


def make_env(tmp_path, late_ms=100, pool_in_files=POOL, timeouts=0, switches=0):
    """Корень с суточным файлом за последние минуты, pool-файл и health.json по записям писателя."""
    now = int(time.time() * 1000)
    root = tmp_path / "root"
    w = DayWriter(str(root), "book00", 1)
    w.control((now - 400_000) * 1_000_000, "subscribed", "1 2")
    for i in range(5):
        for s in pool_in_files:
            rx = now - 300_000 + i * 1000
            w.records(rx * 1_000_000, [book(s, rx - (late_ms if (i == 4 and s == "AVAXUSDT") else 100), 10 + i)])
    w.flush()
    frag = {"shard": "book00", "ts_ms": now, "started_ms": now - 600_000, "with_data": [s.lower() for s in pool_in_files], "connected": 2, "ping_ms": [5.0], "lag_in": [100.0], "lag_out": [3.0],
            "timeouts": [now - 1000] * timeouts, "gaps": [], "reconnects": [], "entrance_switches": [now - 1000] * switches, "last_frame_ms": now, "last_write_ms": now, "fresh": w.fresh.fragment(now)}
    h = health.aggregate([frag], now, POOL, "fake")
    (tmp_path / "health.json").write_text(json.dumps(h))
    (root / "pool-2026-11-12.json").write_text(json.dumps({"pool": POOL}))
    w.close()
    return root, now


def run(tmp_path, root, now):
    out_dir = tmp_path / "out"
    rc = ah.main(["--exchange", "fake", "--root", str(root), "--health", str(tmp_path / "health.json"), "--out-dir", str(out_dir), "--now-ms", str(now + 5000)])
    (f,) = list(out_dir.glob("accept-fake-*.json"))
    return rc, json.loads(f.read_text())


def test_ok_hour_writes_verdict_with_numbers_from_health_and_files(tmp_path):
    root, now = make_env(tmp_path)
    rc, r = run(tmp_path, root, now)
    assert rc == 0 and r["verdict"] == "ok" and r["fail_reasons"] == [] and r["kind"] == "accept_hour"
    assert r["health"]["symbols"] == {"subscribed": 2, "with_data_1m": 2} and r["health"]["timeouts_1h"] == 0 and r["health"]["fresh"]["book"]["late_1h"] == 0
    assert r["files"]["symbols_with_data"]["book"] == 2 and r["files"]["pool_without_book_in_files"] == [] and r["files"]["records"]["b"] == 10
    assert r["window_s"] == 605 and r["files"]["late"]["book"]["late_all"] == 0  # окно — не раньше старта процесса (health.started_ms)


def test_late_records_timeouts_entrance_switch_and_missing_symbols_fail(tmp_path):
    root, now = make_env(tmp_path, late_ms=3000, timeouts=1, switches=2, pool_in_files=["AVAXUSDT"])
    rc, r = run(tmp_path, root, now)
    assert rc == 1 and r["verdict"] == "fail"
    why = " | ".join(r["fail_reasons"])
    assert "timeouts_1h: 1" in why and "entrance_switches_1h: 2" in why and "fresh book" in why and "нет книги по 1 символам" in why
    assert r["files"]["pool_without_book_in_files"] == ["ETHUSDT"]
    assert r["files"]["late"]["book"]["late_all"] == 1 and r["files"]["late"]["book"]["late_after_grace"] == 1 and r["files"]["late"]["book"]["max_ms"] == 3000


def test_stale_health_fails(tmp_path):
    root, now = make_env(tmp_path)
    out_dir = tmp_path / "out"
    rc = ah.main(["--exchange", "fake", "--root", str(root), "--health", str(tmp_path / "health.json"), "--out-dir", str(out_dir), "--now-ms", str(now + 120_000)])
    (f,) = list(out_dir.glob("accept-fake-*.json"))
    assert rc == 1 and "health не обновлялся" in " ".join(json.loads(f.read_text())["fail_reasons"])
