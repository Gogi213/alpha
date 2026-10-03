#!/usr/bin/env python3
"""Экран хода работ (владелец): живой TUI во вкладке терминала. Только рисует `.claude/pulse/status.json`.

    python tools/pulse/pulse.py          # живой экран, обновление раз в 2 с (данные — раз в 5 с); Ctrl+C — выход
    python tools/pulse/pulse.py --once   # один кадр и выход

Данные собирает `collect.py` (без модели); если он не запущен или status.json старше 30 с — поднимается в фоне.
Время — GMT+4. Остановить сборщик: создать файл `.claude/pulse/stop`.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import collect as C  # noqa: E402
from rich.console import Console, Group  # noqa: E402
from rich.live import Live  # noqa: E402
from rich.text import Text  # noqa: E402

MAX_W = 100
BAR = 10


def bar(pct: float) -> list:
    f = max(0, min(BAR, round(pct / 100 * BAR)))
    return [("▓" * f, "green"), ("░" * (BAR - f), "dim")]


def line(parts, w: int) -> Text:
    t = Text.assemble(*parts)
    t.no_wrap = True
    t.truncate(w, overflow="ellipsis")
    return t


def machine_head(m: dict) -> tuple:
    """(текст, стиль) строки машины: имя + ЦП/ОЗУ/диск или состояние связи."""
    name = f"{m['name']:<13}"
    if m["state"] == "off":
        return f"{m['name']} — {m['lines'][0] if m['lines'] else 'выведен'}", "dim"
    if m["state"] == "connecting":
        return f"{name} подключаюсь…", "dim"
    if m["state"] != "ok":
        return f"{name} нет связи", "red"
    bits = []
    if m["cpu"] is not None:
        bits.append(f"ЦП {m['cpu']} %")
    if m["mem"] is not None:
        bits.append(f"ОЗУ {m['mem']} %")
    if m["disk_mb_s"] is not None:
        bits.append(f"диск {m['disk_mb_s']} МБ/с")
    return f"{name} " + " · ".join(bits), ""


def frame(st, w: int, height: int | None = None) -> list:
    """Кадр из status.json. Если не влезает по высоте — сначала меньше строк процессов, потом меньше событий."""
    now = datetime.now(C.TZ)
    if not st:
        return [line([(f"alpha · {now:%H:%M} GMT+4", "bold")], w), Text(""),
                line([("собираю данные… (поднимаю сборщик)", "dim")], w)]
    age = time.time() - float(st.get("built_ts", 0))
    machines = {m["id"]: m for m in st.get("machines", [])}
    calc = machines.get("calc")
    for max_proc, max_ev in ((4, 6), (2, 6), (1, 4), (0, 3), (0, 1)):
        out = []
        left = f"alpha · {now:%H:%M} GMT+4"
        if calc is None or calc["state"] == "connecting":
            right, rst = "сервер: подключаюсь…", "dim"
        elif calc["state"] != "ok":
            right, rst = "сервер: нет связи", "red"
        else:
            right = f"сервер: ЦП {calc['cpu'] if calc['cpu'] is not None else '…'} % · " \
                    f"диск {calc['disk_mb_s'] if calc['disk_mb_s'] is not None else '…'} МБ/с"
            rst = ""
        if len(left) + 2 + len(right) <= w:
            out.append(line([(left, "bold"), (" " * (w - len(left) - len(right)), ""), (right, rst)], w))
        else:
            out += [line([(left, "bold")], w), line([(right, rst)], w)]
        if age > C.STALE_S:
            out.append(line([(f"данные устарели на {int(age)} с — сборщик не отвечает", "red")], w))
        if st.get("error"):
            out.append(line([("сборщик: " + st["error"], "red")], w))
        out += [Text(""), line([("ЦЕЛЬ: ", "bold"), (st.get("goal") or "—", "")], w), Text("")]

        rows = st.get("tickets", [])
        if not rows:
            out.append(line([("нет активных задач", "dim")], w))
        for r in rows:
            head = f"{r['id']:<6}  {r['title']:<24}  {r['role']:<13}  "
            jobs = r.get("jobs", [])
            live_s = [("● " + r["status"], "green")] if r.get("live") else [(r["status"], "dim")]
            if len(jobs) == 1:
                j = jobs[0]
                right = ([("● ", "green")] if r.get("live") else []) + bar(j["pct"]) + [("  " + j["text"], "")]
            else:
                right = live_s
            out.append(line([(head, "")] + right, w))
            if len(jobs) > 1:
                for j in jobs:
                    out.append(line([(" " * 8, "")] + bar(j["pct"]) + [("  " + j["text"], "")], w))
            if r.get("next"):
                out.append(line([(" " * 8 + "└ дальше: " + r["next"], "dim")], w))
        free = st.get("roles_free", [])
        if free:
            out.append(line([("      ".join(f"{x}   свободен" for x in free), "dim")], w))

        out += [Text(""), line([("машины", "dim")], w)]
        for mid in ("calc", "vps", "collector", "pc", "deck"):
            m = machines.get(mid)
            if not m:
                continue
            txt, sty = machine_head(m)
            out.append(line([(txt, sty)], w))
            if m["state"] == "ok":
                for ln in m["lines"][:max_proc]:
                    out.append(line([("  · " + ln, "dim")], w))

        out += [Text(""), line([("последние события", "dim")], w)]
        for e in st.get("events", [])[-max_ev:]:
            out.append(line([(f"{e['time']}  ", "dim"), (f"{e['who']:<6}  ", ""), (e["text"], "")], w))
        if height is None or len(out) <= height - 1:
            break
    return out


def wait_ready(timeout: float = 30.0):
    """Для --once: дождаться свежего кадра, где машины уже посчитали ЦП (окно процессов ≥ 4 с)."""
    t0 = time.time()
    st = None
    while time.time() - t0 < timeout:
        C.ensure_collector()
        st = C.read_status()
        if C.status_fresh(st):
            ms = [m for m in st["machines"] if m["id"] in ("calc", "vps", "collector")]
            if all(m["state"] == "down" or (m["state"] == "ok" and m["cpu"] is not None and
                                              (m["cpu"] is not None and all(g["cpu"] is not None for g in m["procs"])))
                   for m in ms):
                return st
        time.sleep(1)
    return st


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    once = "--once" in sys.argv[1:]
    console = Console(width=None if sys.stdout.isatty() else MAX_W)
    if once:
        st = wait_ready()
        console.print(Group(*frame(st, MAX_W)), soft_wrap=False)
        return 0
    C.ensure_collector()
    try:
        with Live(console=console, screen=True, auto_refresh=False) as live:
            while True:
                C.ensure_collector()
                size = console.size
                live.update(Group(*frame(C.read_status(), min(size.width, MAX_W), size.height)), refresh=True)
                time.sleep(2)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
