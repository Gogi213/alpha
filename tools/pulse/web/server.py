#!/usr/bin/env python3
"""Веб-табло alpha для владельца (счётный сервер, юнит `alpha-board`): только стандартная библиотека Python.

Маршруты — только под секретным префиксом `/<токен>/` (токен — /etc/alpha-board/token), всё остальное — 404:
    GET  /<токен>/              «Диспетчерская» (dispetcher.html)
    GET  /<токен>/dispetcher    то же
    GET  /<токен>/phosphor.css, phosphor.js   оболочка и ядро страницы
    GET  /<токен>/status.json   {view2, built_at, age_s} — последняя сводка (её кладёт bridge.py в /data/board/status.json)
    POST /<токен>/answer        {id, key} — ответ владельца: вопрос и вариант должны быть в текущей сводке;
                                строка JSON дописывается в /data/board/answers.jsonl (≤ 30 в час, один ответ на вопрос)
Ответы забирает ПК (collect.py → bridge.py → `ask.py answer`); сам сервер ничего не исполняет. Токен в логи не пишется.
"""
from __future__ import annotations

import hmac
import json
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CODE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("BOARD_DATA", "/data/board"))
TOKEN_FILE = Path(os.environ.get("BOARD_TOKEN_FILE", "/etc/alpha-board/token"))
PORT = int(os.environ.get("BOARD_PORT", "8787"))
STATUS = DATA / "status.json"
ANSWERS = DATA / "answers.jsonl"
MAX_PER_HOUR = 30
MAX_BODY = 4096
TZ = timezone(timedelta(hours=4))  # GMT+4, как на табло
LOCK = threading.Lock()
TOKEN = TOKEN_FILE.read_text(encoding="utf-8").strip()
if len(TOKEN) < 24:
    sys.exit("токен короче 24 символов — выпустите новый")
TOKEN_B = TOKEN.encode()
_answers_cache: dict = {"key": None, "rows": []}
HTML = "text/html; charset=utf-8"
# маршрут после токена → (файл рядом с этим, тип); только этот список отдаётся с диска
PAGES = {
    "/": ("dispetcher.html", HTML),
    "/dispetcher": ("dispetcher.html", HTML),
    "/phosphor.css": ("phosphor.css", "text/css; charset=utf-8"),
    "/phosphor.js": ("phosphor.js", "application/javascript; charset=utf-8"),
}


def read_answers() -> list:
    """Строки answers.jsonl (кэш по mtime+размер)."""
    try:
        st = ANSWERS.stat()
    except OSError:
        return []
    key = (st.st_mtime_ns, st.st_size)
    if _answers_cache["key"] != key:
        rows = []
        for ln in ANSWERS.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if isinstance(r, dict) and r.get("id"):
                rows.append(r)
        _answers_cache.update(key=key, rows=rows)
    return _answers_cache["rows"]


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
    web = {r["id"]: str(r.get("key", "")) for r in read_answers()}
    v2 = dict(v2)
    v2["questions"] = [dict(q, web_answer=web[q["id"]]) if q.get("id") in web else q for q in v2.get("questions") or []]
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

    def do_POST(self):
        if self._rest() != "/answer":
            return self._404()
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n <= 0 or n > MAX_BODY:
            return self._json(400, {"ok": False, "error": "тело запроса пустое или слишком большое"})
        try:
            req = json.loads(self.rfile.read(n).decode("utf-8"))
            qid, key = req["id"], req["key"]
            if not isinstance(qid, str) or not isinstance(key, str) or len(qid) > 120 or len(key) > 40:
                raise ValueError
        except (ValueError, KeyError, TypeError, UnicodeDecodeError):
            return self._json(400, {"ok": False, "error": "нужен JSON {id, key}"})
        key = key.strip().lower()
        with LOCK:
            doc, err = load_status()
            q = next((q for q in (doc["view2"].get("questions") or []) if q.get("id") == qid), None) if doc else None
            if q is None:
                return self._json(404, {"ok": False, "error": "такого вопроса сейчас нет"})
            opt = next((o for o in q.get("options") or [] if str(o.get("key", "")).lower() == key), None)
            if opt is None:
                return self._json(400, {"ok": False, "error": "у вопроса нет такого варианта"})
            rows = read_answers()
            if any(r["id"] == qid for r in rows):
                return self._json(409, {"ok": False, "error": "ответ на этот вопрос уже принят"})
            if sum(1 for r in rows if r.get("t", 0) > time.time() - 3600) >= MAX_PER_HOUR:
                return self._json(429, {"ok": False, "error": f"не больше {MAX_PER_HOUR} ответов в час"})
            rec = {"ts": datetime.now(TZ).isoformat(timespec="seconds"), "t": int(time.time()), "id": qid, "key": key}
            DATA.mkdir(parents=True, exist_ok=True)
            with open(ANSWERS, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
        print(f"answer {qid} {key}", flush=True)
        self._json(200, {"ok": True, "label": opt.get("label")})

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = lambda self: self._404()


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
