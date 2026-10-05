#!/usr/bin/env python3
"""Веб-табло alpha для владельца (счётный сервер, юнит `alpha-board`): только стандартная библиотека Python.

Маршруты — только под секретным префиксом `/<токен>/` (токен — /etc/alpha-board/token), всё остальное — 404:
    GET  /<токен>/              «Диспетчерская» (dispetcher.html)
    GET  /<токен>/dispetcher    то же
    GET  /<токен>/phosphor.css, phosphor.js   оболочка и ядро страницы
    GET  /<токен>/status.json   {teams:[{id,name,view2,built_at,age_s}], view2, built_at, age_s} — сводки команд;
                                верхний view2/built_at/age_s — команда alpha (старый формат; её кладёт bridge.py в /data/board/status.json)
    POST /<токен>/ingest        сводка команды: заголовок X-Board-Key, тело {"view2": …, "built_at": …} → /data/board/teams/<id>.json
    POST /<токен>/teams/<id>/delete  удалить команду: ключ отзывается, сводка стирается (alpha — 403)
    POST /<токен>/teams         {"name": "…"} → {id, name, key}: новая команда, ключ показывается один раз (хранится только его sha256)
Остальные POST (в том числе /answer) — 404, ответы на вопросы даются в чате с CEO. Сервер ничего не исполняет.
Команда alpha — встроенная (мост bridge.py). Реестр других — /data/board/teams.json. Токен и ключи в логи не пишутся.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CODE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("BOARD_DATA", "/data/board"))
TOKEN_FILE = Path(os.environ.get("BOARD_TOKEN_FILE", "/etc/alpha-board/token"))
PORT = int(os.environ.get("BOARD_PORT", "8787"))
STATUS = DATA / "status.json"
TEAMS_FILE = DATA / "teams.json"
TEAMS_DIR = DATA / "teams"
MAX_TEAMS = 20
MAX_BODY = 4 << 20
LOCK = threading.Lock()
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


def _read_view(path: Path):
    """→ {view2, built_at, age_s} или None."""
    try:
        st = path.stat()
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    v2 = d.get("view2") if isinstance(d, dict) else None
    if not isinstance(v2, dict):
        return None
    return {"view2": v2, "built_at": d.get("built_at"), "age_s": max(0, int(time.time() - st.st_mtime))}


def load_teams() -> list:
    try:
        d = json.loads(TEAMS_FILE.read_text(encoding="utf-8"))
        return [t for t in d.get("teams", []) if isinstance(t, dict) and t.get("id") and t.get("key_sha256")]
    except (OSError, ValueError, AttributeError):
        return []


def alpha_name() -> str:
    try:
        return str(json.loads(TEAMS_FILE.read_text(encoding="utf-8")).get("alpha_name") or "alpha")
    except (OSError, ValueError, AttributeError):
        return "alpha"


def load_status():
    """→ ({teams, view2, built_at, age_s}, None) или (None, текст ошибки). Наружу — только view2, built_at, имена."""
    teams = []
    a = _read_view(STATUS)
    if a:
        teams.append({"id": "alpha", "name": alpha_name(), **a})
    for t in load_teams():
        v = _read_view(TEAMS_DIR / (t["id"] + ".json"))
        teams.append({"id": t["id"], "name": t.get("name") or t["id"], **(v or {"view2": None, "built_at": None, "age_s": None})})
    if not a and not any(t.get("view2") for t in teams):
        return None, "сводки ещё нет"
    top = a or {"view2": None, "built_at": None, "age_s": None}
    return {"teams": teams, **top}, None


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8", "replace")).hexdigest()


def team_by_key(key: str):
    h = key_hash(key)
    found = None
    for t in load_teams():  # без раннего выхода: время не зависит от места совпадения
        if hmac.compare_digest(h, str(t["key_sha256"])):
            found = t
    return found


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def new_team(name: str) -> dict | None:
    """Новая команда → {id, name, key} (ключ — только здесь) или None, если лимит."""
    name = re.sub(r"\s+", " ", name).strip()[:40] or "Команда"
    with LOCK:
        teams = load_teams()
        if len(teams) >= MAX_TEAMS:
            return None
        ids = {t["id"] for t in teams} | {"alpha"}
        tid = "t" + secrets.token_hex(4)
        while tid in ids:
            tid = "t" + secrets.token_hex(4)
        key = "rpv_" + secrets.token_hex(16)
        teams.append({"id": tid, "name": name, "key_sha256": key_hash(key), "created": int(time.time())})
        try:
            alpha = alpha_name()
        except Exception:
            alpha = "alpha"
        atomic_write(TEAMS_FILE, json.dumps({"alpha_name": alpha, "teams": teams}, ensure_ascii=False).encode("utf-8"))
    return {"id": tid, "name": name, "key": key}


def delete_team(team_id: str) -> bool:
    """Снять команду с реестра (ключ отзывается) и стереть её сводку. alpha встроенная — не удаляется."""
    with LOCK:
        teams = load_teams()
        rest = [t for t in teams if t["id"] != team_id]
        if len(rest) == len(teams):
            return False
        atomic_write(TEAMS_FILE, json.dumps({"alpha_name": alpha_name(), "teams": rest}, ensure_ascii=False).encode("utf-8"))
        try:
            (TEAMS_DIR / (team_id + ".json")).unlink()
        except OSError:
            pass
    return True


def ingest(team_id: str, raw: bytes) -> bool:
    try:
        d = json.loads(raw)
    except ValueError:
        return False
    if not isinstance(d, dict) or not isinstance(d.get("view2"), dict):
        return False
    with LOCK:
        atomic_write(TEAMS_DIR / (team_id + ".json"), json.dumps({"view2": d["view2"], "built_at": d.get("built_at")}, ensure_ascii=False).encode("utf-8"))
    return True


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

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        return self.rfile.read(n) if 0 < n <= MAX_BODY else None

    def do_POST(self):
        rest = self._rest()
        if rest == "/ingest":
            team = team_by_key(self.headers.get("X-Board-Key", ""))
            body = self._body() if team else None
            if team is None:
                return self._404()
            if body is None or not ingest(team["id"], body):
                return self._json(400, {"error": "ждём {view2: {...}} до 4 МБ"})
            return self._json(200, {"ok": True})
        if rest == "/teams":
            body = self._body()
            try:
                name = str(json.loads(body).get("name", "")) if body else ""
            except (ValueError, AttributeError):
                name = ""
            t = new_team(name)
            if t is None:
                return self._json(409, {"error": f"не больше {MAX_TEAMS} команд"})
            return self._json(200, t)
        m = re.fullmatch(r"/teams/(t[0-9a-f]{8})/delete", rest or "")
        if m:
            return self._json(200, {"ok": True}) if delete_team(m.group(1)) else self._404()
        if rest == "/teams/alpha/delete":
            return self._json(403, {"error": "alpha встроенная команда — не удаляется"})
        self._404()

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = lambda self: self._404()


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
