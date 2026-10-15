#!/usr/bin/env python3
"""Форма запуска бэктеста (TK-241, В-4, В-7): http.server, только стандартная библиотека.
  python tools/launch/server.py --port 8791                      # разработка: 127.0.0.1, без токена
  LAUNCH_TOKEN_FILE=/etc/alpha-launch/token LAUNCH_HOST=0.0.0.0 python3 server.py --port 8791   # calc, юнит alpha-launch
С токеном все маршруты — только под секретным префиксом `/<токен>/` (как alpha-board), остальное — 404. Форма ИСПОЛНЯЕТ, поэтому:
значения только из белых списков (launch.validate), команда собирается кодом (launch.queue_cmd), пользовательской строки в shell нет,
одна постановка за раз (замок), потолок хешей за запуск (config.json). Токен в логи не пишется. Вся логика — launch.py (тот же код у CLI/CEO).
Нейросетям ничего не уходит (В-6)."""
import argparse, hmac, http.server, json, os, sys, threading, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import launch

HOST = "127.0.0.1"
MAX_BODY = 1 << 20
_cache = {}
_submit_lock = threading.Lock()


def cached(key, fn):
    """meta тяжёлая; сбрасывается при смене results.jsonl (rsync снимка Летописи)."""
    try:
        stamp = os.stat(os.path.join(launch.REG, "results.jsonl")).st_mtime_ns
    except OSError:
        stamp = 0
    if _cache.get(key, (None,))[0] != stamp:
        _cache[key] = (stamp, fn())
    return _cache[key][1]


def read_token(path):
    t = open(path, encoding="utf-8").read().strip()
    if len(t) < 24:
        raise SystemExit("токен короче 24 символов — выпустите новый")
    return t


class Handler(http.server.BaseHTTPRequestHandler):
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
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
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
                return self._send(200, open(os.path.join(HERE, "page.html"), "rb").read(), "text/html; charset=utf-8")
            if path == "/api/meta":
                return self._send(200, cached("meta", launch.meta))
            if path == "/api/cov":
                return self._send(200, {"months": launch.load_config()["months"], "cov": launch.coverage(), "off": launch.OFF_LABELS})
            if path == "/api/letopis":
                return self._send(200, launch.letopis(q.get("hyp"), q.get("month"), q.get("status")))
            if path == "/api/status":
                try:
                    st = launch.status()
                except Exception as e:
                    st = {"text": f"calc недоступен: {e}", "busy": 0, "warn": False}
                return self._send(200, st | {"runs": launch.recent_runs()})
            if path == "/api/runs":   # «Прогоны»: файлы конкретных прогонов + alsched ps; без обходов /data
                try:
                    txt = launch.status()["text"]
                except Exception as e:
                    txt = ""
                    note = f"calc недоступен: {e}"
                else:
                    note = None
                return self._send(200, launch.runs_view(ps_text=txt) | {"ps_error": note})
            return self._send(404, {"error": "not found"})
        except Exception as e:
            return self._send(500, {"error": str(e)})

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
    print(f"форма запуска: {host}:{srv.server_address[1]} ({'под токеном' if token else 'только локально'})", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
