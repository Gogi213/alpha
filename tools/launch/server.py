#!/usr/bin/env python3
"""Форма запуска бэктеста (TK-241, В-4, В-7): http.server, только стандартная библиотека.
  python tools/launch/server.py --port 8791                      # разработка: 127.0.0.1, без токена
  LAUNCH_TOKEN_FILE=/etc/alpha-launch/token LAUNCH_HOST=0.0.0.0 python3 server.py --port 8791   # calc, юнит alpha-launch
С токеном все маршруты — только под секретным префиксом `/<токен>/` (как alpha-board), остальное — 404. Форма ИСПОЛНЯЕТ, поэтому:
значения только из белых списков (launch.validate), команда собирается кодом (launch.queue_cmd), пользовательской строки в shell нет,
одна постановка за раз (замок), потолок хешей за запуск (config.json). Токен в логи не пишется. Вся логика — launch.py (тот же код у CLI/CEO).
Нейросетям ничего не уходит (В-6)."""
import argparse, gzip, hashlib, hmac, http.server, json, os, sys, threading, time, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import launch

HOST = "127.0.0.1"
MAX_BODY = 1 << 20
_cache = {}
_fns = {}
_watching = False
_cache_lock = threading.Lock()
_build_locks = {}   # по замку на ключ: холодный запрос и прогрев не считают одно и то же дважды
_submit_lock = threading.Lock()
_cond = threading.Lock()   # события для /api/events (SSE): версия растёт при каждой подмене кэша
_subs = set()   # по Event на каждое открытое соединение SSE
_ver = 0
_log = []   # [(версия, имя ключа)] — последние подмены
LIGHT = ("status", "runs")   # статус calc и «Прогоны»: свой штамп (задания alsched, каталоги прогонов), тяжёлые ключи его не ждут


def _mt(p):
    try:
        return os.stat(p).st_mtime_ns
    except OSError:
        return 0


def _stamp():
    """Штамп входов Летописи и meta: mtime снимка (results/sieve/metrics) и overlay.jsonl каждого прогона. run.json и запросы формы сюда
    НЕ входят (их читают только «Прогоны», статус и cov): новая заявка не пересчитывает Летопись. Только stat файлов формы, без обходов /data."""
    cfg = launch.load_config()
    st = [_mt(os.path.join(launch.REG, n)) for n in ("results.jsonl", "sieve.jsonl", "metrics.jsonl")]
    try:
        names = sorted(os.listdir(cfg["run_dir"]))
    except OSError:
        names = []
    st += [_mt(os.path.join(cfg["run_dir"], n, "overlay.jsonl")) for n in names]
    return tuple(st)


def _stamp_cov():
    """Штамп cov: входы Летописи + run.json каждого прогона (поле new_hm — «в очереди») и состав каталога прогонов."""
    cfg = launch.load_config()
    try:
        names = sorted(os.listdir(cfg["run_dir"]))
    except OSError:
        names = []
    return (_stamp(), hash(tuple(names))) + tuple(_mt(os.path.join(cfg["run_dir"], n, "run.json")) for n in names)


def _stamp_light():
    """Штамп статуса/«Прогонов»: каталог заданий alsched, запросы, каталоги out/psim каждого прогона (stat десятков путей, без обходов /data)."""
    cfg = launch.load_config()
    st = []
    paths = [os.path.join(os.path.dirname(cfg["alsched"]), "jobs"), cfg["request_dir"], cfg["run_dir"]]
    try:
        paths += [os.path.join(cfg["run_dir"], n, sub) for n in sorted(os.listdir(cfg["run_dir"])) for sub in ("", "out", "psim")]
    except OSError:
        pass
    for p in paths:
        st.append(_mt(p))
    return tuple(st)


def _group(key):
    return key if isinstance(key, str) else key[0]


def _stamp_for(key):
    g = _group(key)
    return _stamp_light() if g in LIGHT else _stamp_cov() if g == "cov" else _stamp()


def _bump(key):
    global _ver
    with _cond:
        _ver += 1
        _log.append((_ver, _group(key)))
        del _log[:-200]
        for ev in _subs:
            ev.set()


def _build(key, fn):
    stamp = _stamp_for(key)
    data = json.dumps(fn(), ensure_ascii=False).encode("utf-8")
    ent = (data, gzip.compress(data, 5), '"' + hashlib.md5(data).hexdigest() + '"')
    with _cache_lock:
        old = _cache.get(key)
        _cache[key] = (stamp, ent)
    if old is None or old[1][2] != ent[2]:   # событие — только если ответ изменился (ETag)
        _bump(key)
    return ent


def cached(key, fn):
    """Готовые байты ответа (JSON + gzip + ETag). Холодный ключ считается один раз; дальше при работающем watch() запрос всегда отдаёт
    готовую версию (пересчёт — фоном по смене штампа входов), без watch() — сверяет штамп сам (тесты)."""
    stamp = None if _watching else _stamp_for(key)
    with _cache_lock:
        _fns[key] = fn
        hit = _cache.get(key)
        if hit and (_watching or hit[0] == stamp):
            return hit[1]
        lock = _build_locks.setdefault(key, threading.Lock())
    with lock:   # другой поток уже считает этот ключ — ждём его результат, а не считаем второй раз
        with _cache_lock:
            hit = _cache.get(key)
            if hit and (_watching or hit[0] == stamp):
                return hit[1]
        return _build(key, fn)


PRIO = {"status": 0, "runs": 1, "meta": 2, "cov": 3}   # порядок пересчёта: лёгкие и видимые первыми, Летопись (остальное) последней


def refresh():
    """Пересчитать устаревшие ключи (по смене штампа своей группы входов); лёгкие (status, runs) — первыми, событие уходит сразу после каждого.
    Старая версия отдаётся, пока считается новая, подмена атомарна."""
    stamps = {"light": _stamp_light(), "cov": _stamp_cov(), "reg": _stamp()}
    grp = lambda k: "light" if _group(k) in LIGHT else "cov" if _group(k) == "cov" else "reg"
    with _cache_lock:
        todo = [(k, f) for k, f in _fns.items() if _cache.get(k, (None,))[0] != stamps[grp(k)]]
    todo.sort(key=lambda kf: PRIO.get(_group(kf[0]), 9))
    for k, f in todo:
        _build(k, f)
    return len(todo)


def _watch_dirs():
    cfg = launch.load_config()
    return [(launch.REG, 1), (cfg["request_dir"], 0), (cfg["run_dir"], 3), (os.path.join(os.path.dirname(cfg["alsched"]), "jobs"), 0)]


def watch(period=1.0):
    """Фон: пересчёт кэша ПО СОБЫТИЮ файловой системы (inotify на каталоги формы, watcher.py), страховка — тот же refresh() раз в 30 с;
    нет inotify (Windows) — stat-опрос штампов раз в period с. Смена штампа → refresh(): старая версия отдаётся, пока считается новая."""
    global _watching
    _watching = True
    try:
        import watcher
        w = watcher.Watcher(_watch_dirs())
        print(f"watch: inotify, {len(w.wd)} каталогов", file=sys.stderr, flush=True)
    except Exception as e:
        w = None
        print(f"watch: inotify нет ({e}) — stat-опрос раз в {period} с", file=sys.stderr, flush=True)
    while True:
        try:
            if w is not None:
                if w.wait(30.0):
                    time.sleep(0.2)   # пачка событий (запись done-маркеров) — один пересчёт
                    while w.wait(0):
                        pass
            else:
                time.sleep(period)
            refresh()
        except Exception as e:
            print(f"watch: {e}", file=sys.stderr, flush=True)
            time.sleep(period)


def prewarm():
    """Прогрев при старте: meta, cov и Летопись целиком — первый запрос страницы отдаёт готовые байты."""
    for key, fn in (("status", _status), ("runs", _runs), ("meta", launch.meta), ("cov", _cov),
                    (("letopis", None, None, None), lambda: launch.letopis())):
        try:
            cached(key, fn)
        except Exception as e:
            print(f"prewarm {key}: {e}", file=sys.stderr, flush=True)


_page = {}   # {(штамп page.html, etag частей): готовые байты страницы с вшитым первым экраном}
BOOT_MARK = b"/*BOOT*/null"


def page_ent():
    """Страница с вшитым первым экраном (meta, cov, status, runs — готовые байты из кэша, что уже посчитано): один запрос вместо пяти (TK-246 п.4).
    ETag — от частей и самой страницы; в page.html нет метки BOOT_MARK — отдаётся как есть."""
    path = os.path.join(HERE, "page.html")
    tpl = open(path, "rb").read()
    if BOOT_MARK not in tpl:
        data = tpl
        return (data, gzip.compress(data, 5), '"' + hashlib.md5(data).hexdigest() + '"')
    with _cache_lock:   # вшивается только готовое: холодная страница не ждёт расчёта (под CPUQuota — секунды), недостающее страница берёт по сети
        parts = [(f"api/{k}", _cache[k][1]) for k in ("meta", "cov", "status", "runs") if k in _cache]
    key = (hashlib.md5(tpl).hexdigest(), tuple(e[2] for _, e in parts))
    hit = _page.get("ent")
    if hit and hit[0] == key:
        return hit[1]
    boot = b"{" + b",".join(b'"' + k.encode() + b'":' + e[0] for k, e in parts) + b"}"
    data = tpl.replace(BOOT_MARK, boot.replace(b"</", rb"<\/"), 1)
    ent = (data, gzip.compress(data, 5), '"' + hashlib.md5(data).hexdigest() + '"')
    _page["ent"] = (key, ent)
    return ent


def _status():
    try:
        st = launch.status()
    except Exception as e:
        st = {"text": f"calc недоступен: {e}", "busy": 0, "warn": False}
    return st | {"runs": launch.recent_runs()}


def _runs():   # одна `alsched ps` на оба ключа: текст берётся из кэша статуса
    st = json.loads(cached("status", _status)[0])
    err = st["text"] if st["text"].startswith("calc недоступен") else None
    return launch.runs_view(ps_text="" if err else st["text"]) | {"ps_error": err}


def _cov():
    return {"months": launch.load_config()["months"], "cov": launch.coverage(), "off": launch.OFF_LABELS}


def read_token(path):
    t = open(path, encoding="utf-8").read().strip()
    if len(t) < 24:
        raise SystemExit("токен короче 24 символов — выпустите новый")
    return t


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"   # keep-alive: страница берёт meta/cov/letopis/runs по одному соединению
    token = None   # None — режим разработки (корень без префикса)

    def _route(self):
        """-> (маршрут без префикса, query) или None, если токен не совпал."""
        u = urllib.parse.urlparse(self.path)
        path = u.path
        if self.token is not None:
            head, _, rest = path[1:].partition("/")
            if not hmac.compare_digest(head.encode("utf-8"), self.token.encode("utf-8")):
                return None
            path = "/" + rest
        return path, {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        ent = body if isinstance(body, tuple) else None   # (байты, gzip, etag) из cached()
        data = ent[0] if ent else body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        etag = ent[2] if ent else None
        if etag and self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        gz = None
        if "gzip" in (self.headers.get("Accept-Encoding") or ""):
            gz = ent[1] if ent else gzip.compress(data, 5) if len(data) > 10_000 else None
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        if gz is not None:
            data = gz
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache" if etag else "no-store")
        if etag:
            self.send_header("ETag", etag)
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise ValueError("тело слишком большое")
        b = json.loads(self.rfile.read(n) or b"{}")
        if not isinstance(b, dict):
            raise ValueError("ожидался объект JSON")
        return b

    def do_GET(self):
        r = self._route()
        if r is None:
            return self._send(404, {"error": "not found"})
        path, q = r
        try:
            if path in ("/", "/index.html"):
                return self._send(200, page_ent(), "text/html; charset=utf-8")
            if path == "/api/meta":
                return self._send(200, cached("meta", launch.meta))
            if path == "/api/cov":
                return self._send(200, cached("cov", _cov))
            if path == "/api/letopis":
                return self._send(200, cached(("letopis", q.get("hyp"), q.get("month"), q.get("status")),
                                              lambda: launch.letopis(q.get("hyp"), q.get("month"), q.get("status"))))
            if path == "/api/status":
                return self._send(200, cached("status", _status))
            if path == "/api/runs":   # «Прогоны»: готовые байты, пересчёт по событиям каталогов прогонов и заданий alsched
                return self._send(200, cached("runs", _runs))
            if path == "/api/events":
                return self._events()
            return self._send(404, {"error": "not found"})
        except Exception as e:
            return self._send(500, {"error": str(e)})

    def _events(self):
        """SSE: `data: {"v": версия, "keys": [изменившиеся ключи]}` при каждой подмене кэша; `: ka` раз в 25 с держит соединение."""
        self.send_response(200)
        for k, v in (("Content-Type", "text/event-stream; charset=utf-8"), ("Cache-Control", "no-cache"), ("Connection", "close"),
                     ("X-Accel-Buffering", "no"), ("Referrer-Policy", "no-referrer")):
            self.send_header(k, v)
        self.end_headers()
        self.close_connection = True
        ev = threading.Event()
        with _cond:
            seen = _ver
            _subs.add(ev)
        try:
            self.wfile.write(b"retry: 3000\n\n")
            self.wfile.flush()
            while True:
                ev.wait(25)
                ev.clear()
                with _cond:
                    cur, keys = _ver, sorted({k for v, k in _log if v > seen})
                self.wfile.write((f"data: {json.dumps({'v': cur, 'keys': keys})}\n\n" if cur > seen else ": ka\n\n").encode("utf-8"))
                self.wfile.flush()
                seen = cur
        except OSError:
            return
        finally:
            with _cond:
                _subs.discard(ev)

    def do_POST(self):
        r = self._route()
        if r is None or r[0] not in ("/api/plan", "/api/submit"):
            return self._send(404, {"error": "not found"})
        path = r[0]
        try:
            b = self._body()
            hyps, months, axes, groups = b.get("hyps") or [], b.get("months") or [], b.get("axes") or {}, b.get("groups") or []
            launch.validate_groups(groups) if groups else launch.validate(hyps, months, axes)
        except (ValueError, TypeError) as e:
            return self._send(400, {"error": str(e)})
        try:
            if path == "/api/plan":
                p = launch.plan_groups(groups) if groups else launch.plan(hyps, months, axes)
                return self._send(200, {k: v for k, v in p.items() if k != "cells"} | {"summary": launch.summary(p)})
            if not _submit_lock.acquire(blocking=False):
                return self._send(409, {"ok": False, "why": "busy", "message": "постановка уже идёт — подождите"})
            try:
                lines = []
                if b.get("dry_run"):
                    res = launch.submit(hyps, months, axes, True, bool(b.get("confirm_bin")), log=lines.append, groups=groups)
                else:   # юнит без прав на очередь: пишет запрос, в alsched его ставит root-юнит alpha-launch-submit
                    res = launch.request_submit(hyps, months, axes, bool(b.get("confirm_bin")), log=lines.append, groups=groups)
            finally:
                _submit_lock.release()
            return self._send(200, {"ok": res["ok"], "why": res.get("why"), "run_id": res.get("run_id"), "log": lines,
                                    "message": "нечего считать" if res.get("why") == "nothing-to-count" else None})
        except Exception as e:
            return self._send(500, {"error": str(e)})

    def log_message(self, *a):
        pass   # путь запроса содержит токен — не пишем


def make_server(port, host=HOST, token=None):
    h = type("H", (Handler,), {"token": token})
    return http.server.ThreadingHTTPServer((host, port), h)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("LAUNCH_PORT", "8791")))
    o = ap.parse_args(argv)
    tf = os.environ.get("LAUNCH_TOKEN_FILE")
    host = os.environ.get("LAUNCH_HOST", HOST)
    token = read_token(tf) if tf else None
    if host != HOST and token is None:
        raise SystemExit("не на 127.0.0.1 — только с LAUNCH_TOKEN_FILE")
    srv = make_server(o.port, host, token)
    threading.Thread(target=lambda: (prewarm(), watch()), daemon=True).start()   # прогрев и фоновый пересчёт по смене файлов
    print(f"форма запуска: {host}:{srv.server_address[1]} ({'под токеном' if token else 'только локально'})", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
