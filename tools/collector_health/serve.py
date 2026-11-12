"""Страница здоровья коллекторов G-022 (TK-529, В-134).

Читает /data/health/<биржа>.json (формат — docs/findings/collector-health-format-2026-11-11.md),
отдаёт page.html и /api/health. Только стандартная библиотека. Порядок бирж и подписи — exchanges.json,
пороги — thresholds.json (числа назначает Судья; нет файла — строка жёлтая «порог не задан»).
Новая биржа = новый файл в каталоге: строка появляется без правки страницы. Файлы с "kind" ≠ "collector" пропускаются.
"""
import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA = 1


def load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def row_state(doc, age_s, thr):
    """off — файла нет, bad — красная, warn — жёлтая, ok — зелёная.

    Красная: файл старше stale_s или with_data_1m == 0; жёлтая: часть символов молчит, таймауты/переподключения
    за час, переход линии на запасной вход за час (G-022), записи старше fresh.late_ms за час (TK-532), порог устаревания не задан (свежесть не проверить — «свежий» не пишем).
    """
    stale_s = (thr or {}).get("stale_s")
    if stale_s is not None and age_s > stale_s:
        return "bad", f"файл не обновлялся {age_s} с"
    sym = doc["symbols"]
    if sym["with_data_1m"] == 0:
        return "bad", "данные за минуту не пришли ни по одному символу"
    if stale_s is None:
        return "warn", "порог устаревания файла не задан"
    if sym["with_data_1m"] < sym["subscribed"]:
        return "warn", "данные за минуту пришли не по всем символам"
    ws = doc["ws"]
    if ws["timeouts_1h"] or ws["reconnects_1h"]:
        return "warn", "за час были таймауты или переподключения"
    if ws.get("entrance_switches_1h"):
        return "warn", f"за час линии переходили на запасной вход: {ws['entrance_switches_1h']}"
    late = sum(k.get("late_1h", 0) for k in ((doc.get("fresh") or {}).get("kinds") or {}).values())
    if late:
        return "warn", f"за час записей старше {(doc['fresh'].get('late_ms') or 0) // 1000} с: {late}"
    return "ok", "файл свежий, таймаутов за час нет"


def one_row(name, doc, now_ms, thr):
    if not isinstance(doc, dict):
        return {"exchange": name, "state": "bad", "note": "файл не читается"}
    try:
        if doc["schema"] != SCHEMA:
            return {"exchange": name, "state": "bad", "note": "неизвестный формат файла"}
        age_s = max(0, (now_ms - doc["ts_ms"]) // 1000)
        state, note = row_state(doc, age_s, thr)
        return {
            "exchange": name, "state": state, "note": note, "age_s": age_s,
            "symbols": doc["symbols"], "ws": doc["ws"],
            "lag_in_ms": doc["lag_in_ms"], "lag_out_ms": doc["lag_out_ms"], "fresh": doc.get("fresh"),
        }
    except (KeyError, TypeError):
        return {"exchange": name, "state": "bad", "note": "в файле не хватает полей"}


def collect(health_dir, now_ms, expected, thr):
    """Строки: сначала биржи из exchanges.json (нет файла — state off), затем остальные файлы по алфавиту."""
    files = {p.stem: p for p in Path(health_dir).glob("*.json")}
    docs = {ex: load_json(p) for ex, p in files.items()}
    # каталог общий с другими писателями (выгрузка TK-530): чужой kind пропускаем
    docs = {ex: d for ex, d in docs.items() if not (isinstance(d, dict) and d.get("kind", "collector") != "collector")}
    names = {e["id"]: e.get("name", e["id"]) for e in expected}
    order = [e["id"] for e in expected] + sorted(set(docs) - set(names))
    rows = []
    for ex in order:
        if ex in docs:
            row = one_row(ex, docs[ex], now_ms, thr)
        else:
            row = {"exchange": ex, "state": "off"}
        row["name"] = names.get(ex, ex)
        rows.append(row)
    return rows


class Handler(BaseHTTPRequestHandler):
    health_dir = Path("/data/health")
    expected = []
    thresholds = None

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/health":
            rows = collect(self.health_dir, int(time.time() * 1000), self.expected, self.thresholds)
            self._send(200, json.dumps({"rows": rows}, ensure_ascii=False).encode(), "application/json; charset=utf-8")
        elif path in ("/", "/index.html"):
            self._send(200, (HERE / "page.html").read_bytes(), "text/html; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain")

    def log_message(self, *_):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--health-dir", default="/data/health")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8088)
    ap.add_argument("--exchanges", default=str(HERE / "exchanges.json"))
    ap.add_argument("--thresholds", default=str(HERE / "thresholds.json"))
    a = ap.parse_args()
    Handler.health_dir = Path(a.health_dir)
    Handler.expected = load_json(a.exchanges) or []
    Handler.thresholds = load_json(a.thresholds)
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
