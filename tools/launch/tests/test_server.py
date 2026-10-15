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


TOKEN = "t" * 32


def _tok_srv():
    s = server.make_server(0, "127.0.0.1", TOKEN)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


def _code(url, data=None):
    import urllib.error
    try:
        return urllib.request.urlopen(urllib.request.Request(url, data, {"Content-Type": "application/json"})).status
    except urllib.error.HTTPError as e:
        return e.code


def test_token_prefix_only_everything_else_404():
    s = _tok_srv()
    base = f"http://127.0.0.1:{s.server_address[1]}"
    try:
        assert _code(f"{base}/{TOKEN}/") == 200
        assert _code(f"{base}/{TOKEN}/api/status") == 200
        for u in ("/", "/api/meta", "/wrong/", "/" + TOKEN[:-1] + "/", "/api/plan"):
            assert _code(base + u) == 404, u
        assert _code(f"{base}/api/submit", b"{}") == 404
        assert _code(f"{base}/{TOKEN}/api/other", b"{}") == 404
    finally:
        s.shutdown()


def test_post_whitelist_400_and_one_submit_at_a_time():
    s = _tok_srv()
    base = f"http://127.0.0.1:{s.server_address[1]}/{TOKEN}"
    try:
        assert _code(f"{base}/api/plan", json.dumps({"hyps": ["Г-9999"], "months": ["2026-01"]}).encode()) == 400
        assert _code(f"{base}/api/submit", json.dumps({"hyps": ["Г-10"], "months": ["2026-13"]}).encode()) == 400
        assert _code(f"{base}/api/plan", b"[1]") == 400
        server._submit_lock.acquire()
        try:
            assert _code(f"{base}/api/submit", json.dumps({"hyps": ["Г-10"], "months": ["2026-01"]}).encode()) == 409
        finally:
            server._submit_lock.release()
    finally:
        s.shutdown()


def test_public_bind_requires_token(monkeypatch):
    import pytest
    monkeypatch.setenv("LAUNCH_HOST", "0.0.0.0")
    monkeypatch.delenv("LAUNCH_TOKEN_FILE", raising=False)
    with pytest.raises(SystemExit):
        server.main(["--port", "0"])


def test_page_uses_relative_api_paths_and_token_not_logged():
    src = open(os.path.join(LAUNCH, "page.html"), encoding="utf-8").read()
    assert "'/api/" not in src and '"/api/' not in src
    assert "def log_message(self, *a):\n        pass" in open(os.path.join(LAUNCH, "server.py"), encoding="utf-8").read()


def test_page_has_no_external_resources():
    """Приказ владельца 15.10: ни шрифтов Google, ни CDN — только свой origin."""
    html = open(os.path.join(LAUNCH, "page.html"), encoding="utf-8").read()
    assert not re.search(r"https?://|@import|<link[^>]+href=|<script[^>]+src=", html), "внешний ресурс в page.html"


def test_cached_responses_gzip_etag_304_and_keepalive():
    """cov/letopis/meta отдаются готовыми байтами: gzip по Accept-Encoding, ETag → 304 без тела, повтор — из кэша."""
    import http.client, gzip
    s = _srv()
    port = s.server_address[1]
    try:
        c = http.client.HTTPConnection("127.0.0.1", port)
        c.request("GET", "/api/cov", headers={"Accept-Encoding": "gzip"})
        r = c.getresponse()
        raw = r.read()
        assert r.status == 200 and r.getheader("Content-Encoding") == "gzip" and r.getheader("Cache-Control") == "no-cache"
        etag = r.getheader("ETag")
        assert etag and json.loads(gzip.decompress(raw))["cov"]
        c.request("GET", "/api/cov", headers={"Accept-Encoding": "gzip", "If-None-Match": etag})   # то же соединение (keep-alive)
        r2 = c.getresponse()
        assert r2.status == 304 and r2.read() == b""
        c.request("GET", "/api/cov")   # без gzip — тот же JSON
        r3 = c.getresponse()
        assert r3.getheader("Content-Encoding") is None and r3.getheader("ETag") == etag and json.loads(r3.read())["cov"]
    finally:
        s.shutdown()
