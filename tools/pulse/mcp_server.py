#!/usr/bin/env python3
"""MCP-сервер экрана хода работ: stdio, JSON-RPC 2.0 без зависимостей и без модели.

Один инструмент `pulse_status` (без аргументов) → содержимое `.claude/pulse/status.json` (structuredContent + текст).
Если сборщик (`collect.py`) не запущен или status.json старше 30 с — поднимает его в фоне и ждёт свежий кадр (до 20 с).
Запуск (регистрирует владелец): python "<репозиторий>/tools/pulse/mcp_server.py"
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import collect as C  # noqa: E402

PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
TOOL = {
    "name": "pulse_status",
    "description": "Ход работ проекта alpha одним JSON: цель, активные тикеты (роль, прогресс, ETA, следующий шаг), "
                   "свободные роли, машины (ЦП/ОЗУ/диск, процессы, сборки), лента событий; время GMT+4. "
                   "Источник — .claude/pulse/status.json, который раз в 5 с пишет collect.py.",
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    "outputSchema": {"type": "object"},
    "annotations": {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
}


def pulse_status() -> dict:
    C.ensure_collector()
    t0 = time.time()
    st = C.read_status()
    while time.time() - t0 < 20 and not (C.status_fresh(st) and
                                         all(m["state"] != "connecting" for m in st.get("machines", []))):
        time.sleep(0.5)
        st = C.read_status()
    if not st:
        raise RuntimeError("status.json нет: сборщик не поднялся")
    st = dict(st)
    st["stale_s"] = max(0, int(time.time() - float(st.get("built_ts", 0))))
    return st


def handle(msg: dict):
    """Ответ на запрос (dict) или None для уведомления."""
    mid, method = msg.get("id"), msg.get("method", "")
    if mid is None:  # уведомление (notifications/initialized и др.) — без ответа
        return None
    params = msg.get("params") or {}
    if method == "initialize":
        want = params.get("protocolVersion")
        res = {"protocolVersion": want if want in PROTOCOLS else PROTOCOLS[0],
               "capabilities": {"tools": {"listChanged": False}},
               "serverInfo": {"name": "alpha-pulse", "version": "1.0.0"}}
    elif method == "ping":
        res = {}
    elif method == "tools/list":
        res = {"tools": [TOOL]}
    elif method == "tools/call":
        if params.get("name") != TOOL["name"]:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"неизвестный инструмент: {params.get('name')}"}}
        try:
            st = pulse_status()
            res = {"content": [{"type": "text", "text": json.dumps(st, ensure_ascii=False)}], "structuredContent": st}
        except Exception as e:
            res = {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"метод не поддерживается: {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": res}


def main() -> int:
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
            out = handle(msg) if isinstance(msg, dict) else \
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "ожидался объект JSON-RPC"}}
        except ValueError:
            out = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "битый JSON"}}
        if out is not None:
            sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
