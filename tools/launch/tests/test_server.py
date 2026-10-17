import gzip, json, os, re, sys, threading, time, urllib.request

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
        assert p["hm_new"] == 0 and 0 < p["hm_have"] <= p["hm_grid"]   # F2 Г-10 без кодировки (T_m) — в сетке, но не к счёту
        r = _post(port, "/api/submit", {"hyps": ["Г-10"], "months": ["2026-01"], "axes": {}, "confirm_bin": True})
        assert r["ok"] is False and r["message"] == "нечего считать"
    finally:
        s.shutdown()


def test_no_neural_calls_in_code():
    """В-6: ни тикетов, ни claude, ни Telegram из кода запуска."""
    bad = re.compile(r"tickets\.py|\bclaude\b|telegram|\bnotify\b|RPV_TG", re.I)
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


def test_cache_event_refresh_never_blocks_request(monkeypatch):
    """Событие (смена штампа файлов) → refresh() пересчитывает в фоне; пока не пересчитано, запрос отдаёт прежние байты, а не ждёт."""
    stamp = {"v": 1}
    calls = []
    monkeypatch.setattr(server, "_stamp", lambda: stamp["v"])
    monkeypatch.setattr(server, "_watching", True)
    monkeypatch.setattr(server, "_cache", {})
    monkeypatch.setattr(server, "_fns", {})

    def fn():
        calls.append(1)
        return {"n": len(calls)}

    first = server.cached("k", fn)
    assert json.loads(first[0]) == {"n": 1} and server.cached("k", fn) is first and len(calls) == 1
    stamp["v"] = 2   # файл изменился: запрос всё ещё отдаёт готовое, без пересчёта
    assert server.cached("k", fn) is first and len(calls) == 1
    assert server.refresh() == 1 and len(calls) == 2   # фоновый пересчёт по событию
    second = server.cached("k", fn)
    assert json.loads(second[0]) == {"n": 2} and second[2] != first[2] and server.refresh() == 0


def test_light_and_heavy_keys_have_separate_stamps(monkeypatch):
    """Статус/«Прогоны» пересчитываются по своему штампу (задания alsched, каталоги прогонов), тяжёлые ключи (cov/Летопись) — по своему."""
    st = {"h": 1, "l": 1}
    calls = []
    monkeypatch.setattr(server, "_stamp", lambda: st["h"])
    monkeypatch.setattr(server, "_stamp_light", lambda: st["l"])
    monkeypatch.setattr(server, "_watching", True)
    monkeypatch.setattr(server, "_cache", {})
    monkeypatch.setattr(server, "_fns", {})
    server.cached("cov", lambda: calls.append("cov") or {"a": len(calls)})
    server.cached("runs", lambda: calls.append("runs") or {"a": len(calls)})
    st["l"] = 2   # сменился только штамп заданий: тяжёлый ключ не считается заново
    assert server.refresh() == 1 and calls == ["cov", "runs", "runs"]
    st["h"] = 2
    assert server.refresh() == 1 and calls[-1] == "cov"


def test_events_stream_pushes_changed_key(monkeypatch):
    """/api/events: подмена кэша с новым ответом → строка data с именем ключа; одинаковый ответ события не даёт."""
    monkeypatch.setattr(server, "_cache", {})
    monkeypatch.setattr(server, "_stamp_light", lambda: 1)
    s = _srv()
    port = s.server_address[1]
    try:
        r = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/events", timeout=5)
        assert r.headers["Content-Type"].startswith("text/event-stream")
        assert r.readline().startswith(b"retry:") and r.readline() == b"\n"
        server._build("runs", lambda: {"n": 1})
        data = None
        while data is None:
            ln = r.readline()
            data = json.loads(ln[6:]) if ln.startswith(b"data: ") else None
        assert data["keys"] == ["runs"] and data["v"] >= 1
        server._build("runs", lambda: {"n": 1})   # тот же ответ — события нет
        server._build("status", lambda: {"n": 2})
        while True:
            ln = r.readline()
            if ln.startswith(b"data: "):
                break
        assert json.loads(ln[6:])["keys"] == ["status"]
        r.close()
    finally:
        s.shutdown()


def test_inotify_watcher_sees_new_file_and_new_subdir(tmp_path):
    import pytest
    try:
        import watcher
        w = watcher.Watcher([(str(tmp_path), 2)])
    except OSError:
        pytest.skip("нет inotify (не Linux)")
    assert w.wait(0) is False
    (tmp_path / "a.json").write_text("1")
    assert w.wait(2) is True
    (tmp_path / "run").mkdir()
    assert w.wait(2) is True
    while w.wait(0):
        pass
    (tmp_path / "run" / "done").write_text("")   # подкаталог, появившийся после старта, уже под наблюдением
    assert w.wait(2) is True


def test_refresh_builds_light_keys_first_and_letopis_ignores_run_json(monkeypatch, tmp_path):
    """TK-246 п.2: status/runs строятся первыми; новый run.json задевает cov, но не Летопись/meta (их штамп — снимок и overlay.jsonl)."""
    reg, runs = tmp_path / "reg", tmp_path / "runs"
    (runs / "L-1").mkdir(parents=True)
    reg.mkdir()
    for n in ("results.jsonl", "sieve.jsonl", "metrics.jsonl"):
        (reg / n).write_text("{}\n")
    monkeypatch.setattr(server.launch, "REG", str(reg))
    monkeypatch.setattr(server.launch, "load_config", lambda path=None: {"run_dir": str(runs), "request_dir": str(tmp_path / "rq"),
                                                                       "alsched": str(tmp_path / "alsched")})
    heavy, cov = server._stamp(), server._stamp_cov()
    (runs / "L-1" / "run.json").write_text("{}")
    assert server._stamp() == heavy and server._stamp_cov() != cov
    (runs / "L-1" / "overlay.jsonl").write_text("{}\n")
    assert server._stamp() != heavy
    order = []
    monkeypatch.setattr(server, "_cache", {})
    monkeypatch.setattr(server, "_fns", {})
    monkeypatch.setattr(server, "_watching", True)
    for k in (("letopis", None, None, None), "cov", "meta", "runs", "status"):
        server.cached(k, lambda k=k: order.append(k) or {"k": len(order)})
    del order[:]
    monkeypatch.setattr(server, "_stamp", lambda: 7)
    monkeypatch.setattr(server, "_stamp_cov", lambda: 7)
    monkeypatch.setattr(server, "_stamp_light", lambda: 7)
    assert server.refresh() == 5 and order == ["status", "runs", "meta", "cov", ("letopis", None, None, None)]


def test_cold_key_is_built_once_under_concurrency(monkeypatch):
    """TK-246 п.3: холодный запрос и прогрев не считают один ключ дважды — второй ждёт первого."""
    monkeypatch.setattr(server, "_cache", {})
    monkeypatch.setattr(server, "_fns", {})
    monkeypatch.setattr(server, "_build_locks", {})
    monkeypatch.setattr(server, "_watching", True)
    calls = []

    def fn():
        calls.append(1)
        time.sleep(0.2)
        return {"n": 1}

    ts = [threading.Thread(target=server.cached, args=("k", fn)) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(calls) == 1


def test_page_embeds_first_screen_and_escapes_script_end(monkeypatch):
    """TK-246 п.4: страница уже содержит meta/cov/status/runs (один запрос вместо пяти); «</» в данных не рвёт <script>."""
    monkeypatch.setattr(server, "_page", {})
    monkeypatch.setattr(server, "_cache", {})
    monkeypatch.setattr(server, "_watching", True)
    server.cached("meta", lambda: {"x": "</script>"})
    server.cached("cov", lambda: {"c": 1})
    server.cached("status", lambda: {"s": 1})
    server.cached("runs", lambda: {"r": 1})
    ent = server.page_ent()
    html = ent[0].decode("utf-8")
    assert server.BOOT_MARK.decode() not in html and '"api/runs":{"r": 1}' in html and r'"api/meta":{"x": "<\/script>"}' in html
    assert server.page_ent() is ent   # части не менялись — те же байты и ETag
    assert gzip.decompress(ent[1]) == ent[0]


def test_cold_page_does_not_wait_for_calculation(monkeypatch):
    """TK-246 п.3: холодная страница не считает ключи (под CPUQuota — секунды): вшито только уже посчитанное, остальное страница берёт по сети."""
    monkeypatch.setattr(server, "_page", {})
    monkeypatch.setattr(server, "_cache", {})
    monkeypatch.setattr(server, "_watching", True)
    called = []
    monkeypatch.setattr(server.launch, "meta", lambda: called.append("meta") or {})
    monkeypatch.setattr(server, "_cov", lambda: called.append("cov") or {})
    cold = server.page_ent()
    assert not called and b'"api/' not in cold[0]
    server.cached("status", lambda: {"s": 1})
    warm = server.page_ent()
    assert not called and b'"api/status":{"s": 1}' in warm[0] and b'"api/cov"' not in warm[0] and warm[2] != cold[2]
