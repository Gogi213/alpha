#!/usr/bin/env python3
"""Веб-табло alpha для владельца (счётный сервер, юнит `alpha-board`): только стандартная библиотека Python.

Маршруты — только под секретным префиксом `/<токен>/` (токен — /etc/alpha-board/token), всё остальное — 404:
    GET  /<токен>/              «Диспетчерская» (dispetcher.html)
    GET  /<токен>/dispetcher    то же
    GET  /<токен>/phosphor.css, phosphor.js   оболочка и ядро страницы
    GET  /<токен>/status.json   {view2, built_at, age_s} — последняя сводка (её кладёт bridge.py в /data/board/status.json)
Только чтение: POST (в том числе /answer) — 404, ответы на вопросы даются в чате с CEO. Сервер ничего не исполняет.
Мост bridge.py по-прежнему кладёт сводку в /data/board/status.json. Токен в логи не пишется.
"""
from __future__ import annotations

import hmac
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CODE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("BOARD_DATA", "/data/board"))
TOKEN_FILE = Path(os.environ.get("BOARD_TOKEN_FILE", "/etc/alpha-board/token"))
PORT = int(os.environ.get("BOARD_PORT", "8787"))
STATUS = DATA / "status.json"
TOKEN = TOKEN_FILE.read_text(encoding="utf-8").strip()
if len(TOKEN) < 24:
    sys.exit("токен короче 24 символов — выпустите новый")
TOKEN_B = TOKEN.encode()
HTML = "text/html; charset=utf-8"
# маршрут после токена → (файл рядом с этим, тип); только этот список отдаётся с диска
PAGES = {
    "/": ("dispetcher.html", HTML),
    "/dispetcher": ("dispetcher.html", HTML),
    "/phosphor.css": ("phosphor.css", "text/css; charset=utf-8"),
    "/phosphor.js": ("phosphor.js", "application/javascript; charset=utf-8"),
}


def load_status():
    """→ ({view2, built_at, age_s}, None) или (None, текст ошибки). Наружу — только view2 и built_at."""
    try:
        st = STATUS.stat()
        d = json.loads(STATUS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, "сводки ещё нет"
    v2 = d.get("view2") if isinstance(d, dict) else None
    if not isinstance(v2, dict):
        return None, "сводка без view2"
    return {"view2": v2, "built_at": d.get("built_at"), "age_s": max(0, int(time.time() - st.st_mtime))}, None


class H(BaseHTTPRequestHandler):
    server_version = "board"
    sys_version = ""
    timeout = 10  # медленные клиенты не держат поток

    def log_message(self, *a):  # путь содержит токен — стандартный журнал запросов не нужен
        pass

    def _send(self, code: int, ctype: str, body: bytes, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, obj: dict):
        self._send(code, "application/json; charset=utf-8", json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _404(self):
        self._send(404, "text/plain; charset=utf-8", b"not found")

    def _rest(self):
        """Хвост пути после `/<токен>` или None, если префикс не наш."""
        p = self.path.split("?", 1)[0].split("#", 1)[0]
        n = len(TOKEN_B)
        head = p[1:].encode("utf-8", "replace")[:n]
        if not p.startswith("/") or not hmac.compare_digest(head, TOKEN_B):
            return None
        rest = p[1 + len(TOKEN):]
        return rest if rest == "" or rest.startswith("/") else None

    def do_GET(self):
        rest = self._rest()
        if rest is None:
            return self._404()
        if rest == "":
            return self._send(301, "text/plain; charset=utf-8", b"", {"Location": f"/{TOKEN}/"})
        if rest in PAGES:
            name, ctype = PAGES[rest]
            try:
                return self._send(200, ctype, (CODE / name).read_bytes())
            except OSError:
                return self._send(500, "text/plain; charset=utf-8", b"no page")
        if rest == "/status.json":
            doc, err = load_status()
            if doc is None:
                return self._json(503, {"error": err})
            return self._json(200, doc)
        self._404()

    do_HEAD = do_GET

    do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = lambda self: self._404()


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
