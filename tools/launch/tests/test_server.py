import json, os, re, sys, threading, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
LAUNCH = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, LAUNCH)
import server


def _srv():
    s = server.make_server(0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


def _post(port, path, body):
    r = urllib.request.Request(f"http://127.0.0.1:{port}{path}", json.dumps(body).encode("utf-8"), {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(r))


def test_server_listens_only_on_loopback():
    s = _srv()
    try:
        assert s.server_address[0] == "127.0.0.1"
    finally:
        s.shutdown()


def test_api_plan_and_nothing_to_count():
    s = _srv()
    port = s.server_address[1]
    try:
        meta = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/meta"))
        assert len(meta["hyps"]) >= 100 and meta["presets"]["v227"]["ids"] and "не 56" in meta["presets"]["v227"]["note"]
        p = _post(port, "/api/plan", {"hyps": ["Г-10"], "months": ["2026-01"], "axes": {}})
        assert p["hm_new"] == 0 and p["hm_have"] == p["hm_grid"] > 0
        r = _post(port, "/api/submit", {"hyps": ["Г-10"], "months": ["2026-01"], "axes": {}, "confirm_bin": True})
        assert r["ok"] is False and r["message"] == "нечего считать"
    finally:
        s.shutdown()


def test_no_neural_calls_in_code():
    """В-6: ни тикетов, ни claude, ни Telegram из кода запуска."""
    bad = re.compile(r"tickets\.py|\bclaude\b|telegram|notify|RPV_TG", re.I)
    for f in ("launch.py", "server.py", "page.html"):
        for i, line in enumerate(open(os.path.join(LAUNCH, f), encoding="utf-8"), 1):
            if "(В-6)" in line or "нейросет" in line:
                continue
            assert not bad.search(line), f"{f}:{i}: {line.strip()}"
