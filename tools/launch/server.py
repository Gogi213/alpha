#!/usr/bin/env python3
"""Локальная форма запуска бэктеста (TK-241, В-4): http.server, ТОЛЬКО 127.0.0.1.
  python tools/launch/server.py --port 8791
Вся логика — launch.py (тот же код вызывает CLI/CEO). Нейросетям ничего не уходит (В-6)."""
import argparse, http.server, json, os, sys, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import launch

HOST = "127.0.0.1"
_cache = {}


def cached(key, fn):
    if key not in _cache:
        _cache[key] = fn()
    return _cache[key]


class Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        try:
            if u.path in ("/", "/index.html"):
                return self._send(200, open(os.path.join(HERE, "page.html"), "rb").read(), "text/html; charset=utf-8")
            if u.path == "/api/meta":
                return self._send(200, cached("meta", launch.meta))
            if u.path == "/api/letopis":
                return self._send(200, launch.letopis(q.get("hyp"), q.get("month"), q.get("status")))
            if u.path == "/api/status":
                try:
                    return self._send(200, launch.status())
                except Exception as e:
                    return self._send(200, {"text": f"calc недоступен: {e}", "busy": 0, "warn": False})
            return self._send(404, {"error": "not found"})
        except Exception as e:
            return self._send(500, {"error": str(e)})

    def do_POST(self):
        try:
            b = self._body()
            hyps, months, axes = b.get("hyps") or [], b.get("months") or [], b.get("axes") or {}
            if self.path == "/api/plan":
                p = launch.plan(hyps, months, axes)
                return self._send(200, {k: v for k, v in p.items() if k != "cells"} | {"summary": launch.summary(p)})
            if self.path == "/api/submit":
                lines = []
                r = launch.submit(hyps, months, axes, bool(b.get("dry_run")), bool(b.get("confirm_bin")), log=lines.append)
                return self._send(200, {"ok": r["ok"], "why": r.get("why"), "run_id": r.get("run_id"), "log": lines,
                                        "message": "нечего считать" if r.get("why") == "nothing-to-count" else None})
            return self._send(404, {"error": "not found"})
        except Exception as e:
            return self._send(500, {"error": str(e)})

    def log_message(self, *a):
        pass


def make_server(port):
    return http.server.ThreadingHTTPServer((HOST, port), Handler)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8791)
    o = ap.parse_args(argv)
    srv = make_server(o.port)
    print(f"форма запуска: http://{HOST}:{srv.server_address[1]}/ (только локально)", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
