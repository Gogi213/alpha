"""Страница здоровья коллекторов G-022 (TK-529, В-134).

Читает /data/health/<биржа>.json (формат — docs/findings/collector-health-format-2026-11-11.md),
отдаёт page.html, /api/health (прежняя страница, page.html.prev-v3), /api/v3 (столбики по 15 минут, v3.py) и вкладку «Арбитраж»:
/api/arb (таблица) и /api/arb/chart?c=&a=&b= (график пары) — из каталога --arb-dir, который пишет tools/arb_stats/arb_live.py (arb_api.py). Только стандартная библиотека. Порядок бирж и подписи — exchanges.json,
пороги — thresholds.json (числа назначает Судья; нет файла — строка жёлтая «порог не задан»).
Новая биржа = новый файл в каталоге: строка появляется без правки страницы. Файлы с "kind" ≠ "collector" пропускаются.
"""
import argparse
import gzip
import hashlib
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import arb_api
import v3

HERE = Path(__file__).resolve().parent
SCHEMAS = (1, 2)  # 2 — Rust-коллектор (alpha collect): схема 1 + backpressure, process, totals, shards


def load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def row_state(doc, age_s, thr):
    """off — файла нет, bad — красная, warn — жёлтая, ok — зелёная.

    Красная: файл старше stale_s или with_data_1m == 0; жёлтая: часть символов молчит, таймауты/переподключения
    за час, переход линии на запасной вход за час (G-022), записи старше fresh.late_ms за час (TK-532), очередь приёма Rust-коллектора переполнялась в последнее окно, порог устаревания не задан (свежесть не проверить — «свежий» не пишем).
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
    ring = ((doc.get("backpressure") or {}).get("ring") or {})
    if ring.get("full_events_window"):
        return "warn", f"очередь приёма переполнялась за последнее окно: {ring['full_events_window']} раз (приём ждал писателя)"
    late = sum(k.get("late_1h", 0) for k in ((doc.get("fresh") or {}).get("kinds") or {}).values())
    if late and late >= max(1, int((thr or {}).get("late_1h_flag", 1))):
        return "warn", f"за час записей старше {(doc['fresh'].get('late_ms') or 0) // 1000} с: {late}"
    return "ok", "файл свежий, таймаутов за час нет"


def one_row(name, doc, now_ms, thr):
    if not isinstance(doc, dict):
        return {"exchange": name, "state": "bad", "note": "файл не читается"}
    try:
        if doc["schema"] not in SCHEMAS:
            return {"exchange": name, "state": "bad", "note": "неизвестный формат файла"}
        age_s = max(0, (now_ms - doc["ts_ms"]) // 1000)
        state, note = row_state(doc, age_s, thr)
        return {
            "exchange": name, "state": state, "note": note, "age_s": age_s,
            "symbols": doc["symbols"], "ws": doc["ws"],
            "lag_in_ms": doc["lag_in_ms"], "lag_out_ms": doc["lag_out_ms"], "fresh": doc.get("fresh"),
            "backpressure": doc.get("backpressure"), "process": doc.get("process"),
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
    protocol_version = "HTTP/1.1"   # соединение не закрывается после каждого ответа: страница ходит за данными несколько раз подряд
    timeout = 30                    # висящее без запросов соединение освобождает поток
    disable_nagle_algorithm = True  # TCP_NODELAY: без него второй write (тело) ждёт ACK первого (заголовки) — до 200 мс на Windows-клиенте
    wbufsize = 1 << 16              # заголовки и тело уходят одним пакетом (сброс — в конце запроса, handle_one_request)
    health_dir = Path("/data/health")
    expected = []
    thresholds = None
    roots = {}
    net = None
    arb = None
    _page = (None, None)            # (метка файла, (тело, etag, сжатое тело))
    _v3 = (0.0, None)               # (когда посчитан, тело): ответ /api/v3 не чаще раза в 5 с

    def _send(self, code, body, ctype, etag=None, gz=None):
        """Ответ с gzip (если клиент принимает) и ETag/304. etag — готовая метка (иначе хеш тела), gz — готовое сжатое тело."""
        if code == 200:
            etag = etag or '"%s"' % hashlib.md5(body).hexdigest()[:20]
            if self.headers.get("If-None-Match") == etag:
                self.send_response(304)
                self.send_header("ETag", etag)
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        enc = None
        if len(body) > 600 and "gzip" in self.headers.get("Accept-Encoding", ""):
            body, enc = gz or gzip.compress(body, 5), "gzip"
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-cache" if code == 200 else "no-store")
        self.send_header("Vary", "Accept-Encoding")
        if code == 200:
            self.send_header("ETag", etag)
        if enc:
            self.send_header("Content-Encoding", enc)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _page_bytes(self):
        p = HERE / "page.html"
        st = p.stat()
        mark = (st.st_mtime_ns, st.st_size)
        if Handler._page[0] != mark:
            body = p.read_bytes()
            Handler._page = (mark, (body, '"%s"' % hashlib.md5(body).hexdigest()[:20], gzip.compress(body, 6)))
        return Handler._page[1]

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        js = "application/json; charset=utf-8"
        if path == "/api/health":
            rows = collect(self.health_dir, int(time.time() * 1000), self.expected, self.thresholds)
            self._send(200, json.dumps({"rows": rows}, ensure_ascii=False).encode(), js)
        elif path == "/api/v3":
            t, body = Handler._v3
            if body is None or time.time() - t > 5:
                try:
                    body = json.dumps(v3.payload(self.health_dir, self.expected, self.roots, self.net, thr=self.thresholds), ensure_ascii=False).encode()
                except Exception as e:  # страница не должна падать от одного кривого файла
                    body = json.dumps({"error": repr(e)}).encode()
                Handler._v3 = (time.time(), body)
            self._send(200, body, js)
        elif path == "/api/arb":
            body, etag, gz = self.arb.table()
            self._send(200, body, js, etag, gz)
        elif path == "/api/arb/chart":
            body = self.arb.chart(self.path.split("?", 1)[1] if "?" in self.path else "")
            if body is None:
                self._send(404, b'{"error":"no such pair"}', js)
            else:
                self._send(200, body, js)
        elif path in ("/", "/index.html"):
            body, etag, gz = self._page_bytes()
            self._send(200, body, "text/html; charset=utf-8", etag, gz)
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
    ap.add_argument("--roots", default=str(HERE / "roots.json"))
    ap.add_argument("--netwatch", default="/data/netwatch")
    ap.add_argument("--arb-dir", default="/data/arb-tab/live")
    a = ap.parse_args()
    Handler.health_dir = Path(a.health_dir)
    Handler.expected = load_json(a.exchanges) or []
    Handler.thresholds = load_json(a.thresholds)
    Handler.roots = load_json(a.roots) or {}
    Handler.net = v3.Net(a.netwatch)
    Handler.arb = arb_api.Arb(a.arb_dir)
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
