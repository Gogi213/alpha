#!/usr/bin/env python3
"""Экран хода работ (владелец): живой TUI во вкладке терминала. Только рисует `.claude/pulse/status.json`.

    python tools/pulse/pulse.py          # живой экран, перерисовка раз в 2 с (данные — раз в 5 с); Ctrl+C — выход
    python tools/pulse/pulse.py --once   # один кадр и выход (ширина — COLUMNS, иначе 100)

Данные собирает `collect.py` (без модели); если он не запущен или status.json старше 30 с — поднимается в фоне.
Время — GMT+4. Остановить сборщик: создать файл `.claude/pulse/stop`.
"""
from __future__ import annotations

import sys
import textwrap
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
IND = 8  # отступ строк под задачей


def bar(pct: float) -> list:
    f = max(0, min(BAR, round(pct / 100 * BAR)))
    return [("▓" * f, "green"), ("░" * (BAR - f), "dim")]


def line(parts, w: int) -> Text:
    t = Text.assemble(*parts)
    t.no_wrap = True
    t.truncate(w, overflow="ellipsis")
    return t


def clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: max(1, n - 1)].rstrip() + "…"


def wrapped(prefix: str, text: str, w: int, style: str, max_lines: int = 2, first: str | None = None) -> list:
    """Текст с переносом по словам, не больше `max_lines` строк (хвост последней — «…»); продолжение — с отступом prefix."""
    first = prefix if first is None else first
    avail = max(10, w - len(prefix))
    lines = textwrap.wrap(text, avail, break_long_words=True, break_on_hyphens=False) or [""]
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = clip(lines[-1] + "…", avail)
    return [line([((first if i == 0 else prefix) + ln, style)], w) for i, ln in enumerate(lines)]


def job_lines(j: dict, prefix: list, indent: int, w: int) -> list:
    """Строка job: [prefix] «step done/total unit · ещё ~N мин». Усекается только название шага; числа и ETA целы,
    при нехватке места хвост переносится на следующую строку."""
    pre_len = sum(len(t) for t, _ in prefix)
    tail = j["progress"] + (f" · {j['eta']}" if j.get("eta") else "")
    step = j["step"]
    avail = w - indent - pre_len
    sp = " " * indent
    if len(step) + 1 + len(tail) <= avail:
        return [line([(sp, "")] + prefix + [(f"{step} ", ""), (tail, "bold")], w)]
    room = avail - len(tail) - 1
    if room >= 8:
        return [line([(sp, "")] + prefix + [(f"{clip(step, room)} ", ""), (tail, "bold")], w)]
    out = [line([(sp, "")] + prefix + [(clip(step, avail), "")], w)]  # хвост — отдельной строкой
    out += wrapped(sp + " " * pre_len, tail, w, "bold", max_lines=3)
    return out


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


def ticket_lines(r: dict, w: int) -> list:
    out = []
    jobs = r.get("jobs", [])
    live = [("● ", "green")] if r.get("live") else []
    if len(jobs) == 1:
        right = live + bar(jobs[0]["pct"]) + [(f" {jobs[0]['pct']:>3} %", "")]
    elif r.get("live"):
        right = [("● " + r["status"], "green")]
    else:
        right = [(r["status"], "dim")]
    right_len = sum(len(t) for t, _ in right)
    # название ужимается, если не хватает места справа (узкая вкладка), но не уже 10 знаков
    title_w = min(24, w - 6 - 2 - 2 - 13 - 2 - right_len)
    if title_w >= 12:
        head = f"{r['id']:<6}  {clip(r['title'], title_w):<{title_w}}  {r['role']:<13}  "
        out.append(line([(head, "")] + right, w))
    else:  # очень узкая вкладка: статус/полоса — отдельной строкой под названием
        out.append(line([(f"{r['id']:<6}  {clip(r['title'], max(8, w - 6 - 2 - 2 - len(r['role'])))}  {r['role']}", "")], w))
        out.append(line([(" " * IND, "")] + right, w))
    if len(jobs) == 1:
        out += job_lines(jobs[0], [], IND, w)
    else:
        for j in jobs:
            out += job_lines(j, bar(j["pct"]) + [(f" {j['pct']:>3} %  ", "")], IND, w)
    if r.get("next"):
        out += wrapped(" " * (IND + 2), r["next"], w, "dim", first=" " * IND + "└ дальше: ", max_lines=2) \
            if len(r["next"]) + IND + 10 > w else [line([(" " * IND + "└ дальше: " + r["next"], "dim")], w)]
    return out


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
        out += [Text("")] + wrapped("       ", st.get("goal") or "—", w, "", first="ЦЕЛЬ: ") + [Text("")]

        rows = st.get("tickets", [])
        if not rows:
            out.append(line([("нет активных задач", "dim")], w))
        for r in rows:
            out += ticket_lines(r, w)
        free = st.get("roles_free", [])
        if free:
            items = [f"{x}   свободен" for x in free]
            out.append(line([(("      " if sum(map(len, items)) + 12 <= w else "   ").join(items), "dim")], w))

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
            head = f"{e['time']}  {e['who']:<6}  "
            ls = wrapped(" " * len(head), e["text"], w, "", max_lines=2, first=head)
            ls[0] = line([(f"{e['time']}  ", "dim"), (f"{e['who']:<6}  ", ""), (ls[0].plain[len(head):], "")], w)
            out += ls
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
                                              all(g["cpu"] is not None for g in m["procs"])) for m in ms):
                return st
        time.sleep(1)
    return st


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    once = "--once" in sys.argv[1:]
    console = Console()
    if once:
        w = min(console.width if (sys.stdout.isatty() or "COLUMNS" in __import__("os").environ) else MAX_W, MAX_W)
        st = wait_ready()
        console.print(Group(*frame(st, w)), soft_wrap=False, width=w)
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
