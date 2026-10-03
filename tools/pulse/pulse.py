#!/usr/bin/env python3
"""Экран хода работ (владелец): живой TUI во вкладке терминала. Только рисует раздел `view` из `.claude/pulse/status.json`.

    python tools/pulse/pulse.py          # живой экран, перерисовка раз в 2 с (данные — раз в 5 с); Ctrl+C — выход
    python tools/pulse/pulse.py --once   # один кадр и выход (ширина — COLUMNS, иначе 100; цвет — как у терминала)
    python tools/pulse/pulse.py --once --status файл.json   # кадр из готового status.json (проверка вида)
    python tools/pulse/pulse.py --once --color              # принудительно с цветом (вывод в файл/конвейер)

Экран читается сверху вниз; секции отделены линией-заголовком: что идёт (и ГДЕ), что дальше, что ждёт ответа
владельца, на какой машине какая задача идёт (или «⚠ без задачи»), что было. Тексты — человеческие, из
`.claude/pulse/plain.json` (пишет CEO); связь «процесс → задача → машина» считает `collect.py` (`make_view`).
Цвет — только в ключевых местах: голубой (процент, полоса, «ещё ~N мин»), жирный белый (название задачи), зелёный
(«● идёт», «занят»), жёлтый (ждёт ответа, «⚠ без задачи»), красный (нет связи, диспетчер стоит), серый — служебное.
Данные собирает `collect.py` (без модели); если он не запущен или status.json старше 30 с — поднимается в фоне.
Время — GMT+4. Остановить сборщик: файл `.claude/pulse/stop`.
"""
from __future__ import annotations

import json
import os
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
ACC, ACC_BAR = "bold cyan", "cyan"            # процент, «ещё ~N мин», полоса
TITLE = "bold white"                           # название задачи, заголовки секций
GREEN, YELLOW, YELLOW_B, RED, DIM = "green", "yellow", "bold yellow", "bold red", "dim"
STATE_STYLE = {"busy": GREEN, "ok": "", "idle": DIM, "stopped": DIM, "off": DIM, "connecting": DIM,
               "down": RED, "bad": RED}
LEVEL_STYLE = {"ok": GREEN, "bad": RED, "idle": DIM}
NAMEW = 13  # ширина названия машины


def clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: max(1, n - 1)].rstrip() + "…"


def plen(parts) -> int:
    return sum(len(t) for t, _ in parts)


def line(parts, w: int) -> Text:
    t = Text.assemble(*parts)
    t.no_wrap = True
    t.truncate(w, overflow="ellipsis")
    return t


def pad(parts, n: int) -> list:
    return list(parts) + [(" " * max(0, n - plen(parts)), "")]


def clip_parts(parts, n: int) -> list:
    """Куски (текст, стиль) укорачиваются до n знаков, хвост — «…»."""
    if plen(parts) <= n:
        return list(parts)
    out, left = [], max(1, n - 1)
    for t, st in parts:
        if left <= 0:
            break
        out.append((t[:left], st))
        left -= len(t)
    out.append(("…", out[-1][1] if out else ""))
    return out


def wrap(text: str, width: int, max_lines: int = 3) -> list:
    """Перенос по словам; больше `max_lines` строк — хвост последней обрезается «…»."""
    width = max(10, width)
    lines = textwrap.wrap(text, width, break_long_words=True, break_on_hyphens=False) or [""]
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = clip(lines[-1] + "…", width)
    return lines


def wrap_styled(tokens, width: int, max_lines: int = 3) -> list:
    """Перенос по словам для кусков с разным стилем: [(текст, стиль)…] → строки из кусков; лишнее — «…»."""
    width = max(10, width)
    words = [(wd, st) for tx, st in tokens for wd in tx.split()]
    lines: list = [[]]
    cur = 0
    for wd, st in words:
        while len(wd) > width:  # слово длиннее строки
            if cur:
                lines.append([])
                cur = 0
            lines[-1].append((wd[:width], st))
            wd = wd[width:]
            lines.append([])
        add = len(wd) + (1 if cur else 0)
        if cur and cur + add > width:
            lines.append([])
            cur, add = 0, len(wd)
        if cur:
            lines[-1].append((" ", ""))
        lines[-1].append((wd, st))
        cur += add
    lines = [ln for ln in lines if ln] or [[("", "")]]
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = clip_parts(lines[-1] + [("…", "")], width)
    return lines


def bar(pct: float, n: int) -> list:
    f = max(0, min(n, round(pct / 100 * n)))
    return [("█" * f, ACC_BAR), ("░" * (n - f), DIM)]


def rule(title: str, w: int, tstyle: str = TITLE, dstyle: str = DIM, labels=()) -> Text:
    """`── ЗАГОЛОВОК ──────…` на всю ширину; подписи колонок (x, текст) встраиваются в линию на своём месте."""
    parts = [("── ", dstyle), (title, tstyle), (" ", "")]
    pos = 3 + len(title) + 1
    for x, text in labels:
        if x - 1 > pos + 2 and x + len(text) + 2 <= w:
            parts += [("─" * (x - 1 - pos), dstyle), (" ", ""), (text, DIM), (" ", "")]
            pos = x + len(text) + 1
    parts.append(("─" * max(0, w - pos), dstyle))
    return line(parts, w)


def place_parts(p: dict, wide: bool) -> list:
    parts = [(p["name"], "")]
    if p.get("what"):
        parts.append((f" ({p['what']})", DIM))
    if p.get("eta"):
        parts.append((("   " if wide else " · ") + p["eta"], ACC))
    return parts


# --- разделы ----------------------------------------------------------------------------------------------------------
def head_line(view: dict, now: datetime, w: int) -> list:
    hl = view["headline"]
    left = [(" ALPHA", "bold"), (f" · {now:%H:%M}", DIM)]
    right = [(hl["text"], LEVEL_STYLE.get(hl["level"], ""))]
    if view.get("attention"):
        right += [(" · ", DIM), (f"ждёт вашего ответа: {view['attention']}", YELLOW_B)]
    if plen(left) + 2 + plen(right) <= w - 1:
        return [line(left + [(" " * (w - 1 - plen(left) - plen(right)), "")] + right, w)]
    out = [line(left, w)]  # длинный заголовок («есть проблема…») — вторыми строками с переносом
    for ln in wrap(hl["text"], w - 2, 3):
        out.append(line([(" " + ln, LEVEL_STYLE.get(hl["level"], ""))], w))
    if view.get("attention"):
        out.append(line([(f" ждёт вашего ответа: {view['attention']}", YELLOW_B)], w))
    return out


def now_block(rows: list, w: int, roomy: bool) -> list:
    wide = w >= 90
    bar_w = 20 if wide else 10
    a_w = bar_w + 2 + 5
    if wide:
        tc = min(34, max(28, w - 62))
        tb = w - 1 - a_w - 2 - 2 - tc - 1
        out = [rule("СЕЙЧАС ИДЁТ", w, labels=[(1 + a_w + 2 + tb + 2, "ГДЕ")])]
    else:
        tc = 0
        tb = w - 1 - a_w - 2 - 1
        out = [rule("СЕЙЧАС ИДЁТ", w)]
    if not rows:
        return out + [line([(" ничего не идёт", DIM)], w)]
    for ri, r in enumerate(rows):
        if ri and roomy:
            out.append(Text(""))
        if r.get("pct") is not None:
            a = bar(r["pct"], bar_w) + [(f"  {r['pct']:>3} %", ACC)]
        else:
            since = r.get("since_min")
            a = [("● " + (f"идёт {C.fmt_min(since)}" if since is not None else "идёт"), GREEN)]
        tokens = [(r["text"], TITLE)] + ([("·", DIM), (r["detail"], "")] if r.get("detail") else [])
        b = wrap_styled(tokens, tb, 3)
        wh = [place_parts(p, wide) for p in r.get("where", [])]
        if wide:
            c = [clip_parts(x, tc) for x in wh]
            for i in range(max(len(b), len(c), 1)):
                parts = [(" ", "")] + (pad(a, a_w) if i == 0 else [(" " * a_w, "")]) + [("  ", "")]
                parts += pad(b[i], tb) if i < len(b) else [(" " * tb, "")]
                parts += [("  ", "")] + (c[i] if i < len(c) else [])
                out.append(line(parts, w))
        else:
            ind = 1 + a_w + 2
            for i, ln in enumerate(b):
                out.append(line(([(" ", "")] + pad(a, a_w) + [("  ", "")] if i == 0 else [(" " * ind, "")]) + ln, w))
            for i, x in enumerate(wh):
                out.append(line([(" " * ind + ("где: " if i == 0 else "     "), DIM)] + clip_parts(x, tb - 5), w))
    return out


def next_block(items: list, w: int) -> list:
    out = [rule("ДАЛЬШЕ", w)]
    for i, it in enumerate(items, 1):
        for j, ln in enumerate(wrap(it, w - 5, 2)):
            out.append(line([((f" {i}. " if j == 0 else "    "), DIM), (ln, "")], w))
    return out


def ask_block(items: list, w: int) -> list:
    out = [rule("ЖДЁТ ВАШЕГО ОТВЕТА", w, tstyle=YELLOW_B, dstyle=YELLOW)]
    for it in items:
        for j, ln in enumerate(wrap(it, w - 5, 3)):
            out.append(line([((" ?  " if j == 0 else "    ") + ln, YELLOW_B)], w))
    return out


def machines_block(ms: list, w: int, roomy: bool) -> list:
    cpu_w = 8 if w >= 60 else 0  # «ЦП 100 %»
    sw = max(9, *(len(m["state_text"]) for m in ms)) if ms else 9
    xa = 1 + NAMEW + 1 + sw + 2  # колонка стрелки
    tt = w - (xa + 3) - 1 - (cpu_w + 1 if cpu_w else 0)
    out = [rule("МАШИНЫ", w, labels=[(xa, "ДЛЯ КАКОЙ ЗАДАЧИ")])]
    for mi, m in enumerate(ms):
        if mi and roomy:
            out.append(Text(""))
        rows = [("→", x, "") for x in m["items"]] + [("⚠", x, YELLOW) for x in m["warns"]]
        if m.get("note"):
            rows.append(("", m["note"], DIM))
        lines = []
        for mk, tx, sty in rows:
            for i, ln in enumerate(wrap(tx, tt, 3)):
                lines.append((mk if i == 0 else "", ln, sty))
        cpu = f"ЦП {m['cpu']} %" if m.get("cpu") is not None and cpu_w else ""
        for i in range(max(1, len(lines))):
            if i == 0:
                parts = [(" ", ""), (f"{clip(m['name'], NAMEW):<{NAMEW}}", ""), (" ", ""),
                         (f"{m['state_text']:<{sw}}", STATE_STYLE.get(m["state"], "")), ("  ", "")]
            else:
                parts = [(" " * xa, "")]
            if i < len(lines):
                mk, tx, sty = lines[i]
                parts += [((mk + "  ") if mk else "   ", sty or DIM), (tx, sty)]
            if i == 0 and cpu:
                parts = pad(parts, w - 1 - cpu_w) + [(cpu, DIM)]
            out.append(line(parts, w))
    return out


def news_block(items: list, w: int, limit: int, src: str) -> list:
    out = [rule("ЧТО БЫЛО", w)]
    for n in items[:limit]:
        head = f" {n.get('time') or '':<5}  "
        who = f"{n['who']} · " if src == "events" and n.get("who") else ""
        for j, ln in enumerate(wrap(who + n["text"], w - len(head), 2)):
            out.append(line([(head if j == 0 else " " * len(head), DIM), (ln, "")], w))
    if len(out) == 1:
        out.append(line([(" пока ничего", DIM)], w))
    return out


def frame(st, w: int, height: int | None = None) -> list:
    """Кадр из status.json. Не влезает по высоте — сначала убираются пустые строки между задачами/машинами,
    потом «что было» (5 → 3 → 1 → 0)."""
    now = datetime.now(C.TZ)
    if not st:
        return [line([(f" ALPHA · {now:%H:%M}", "bold")], w), Text(""), line([(" собираю данные… (поднимаю сборщик)", DIM)], w)]
    age = time.time() - float(st.get("built_ts", 0))
    view = st.get("view")
    for roomy, limit in ((True, 5), (True, 3), (True, 1), (False, 5), (False, 3), (False, 1), (False, 0)):
        out = []
        if view:
            out += head_line(view, now, w)
        else:
            out.append(line([(f" ALPHA · {now:%H:%M}", "bold")], w))
            out.append(line([(" сборщик старой версии: создайте файл .claude/pulse/stop, экран поднимет новый", RED)], w))
        if age > C.STALE_S:
            out.append(line([(f" данные устарели на {int(age)} с — сборщик не отвечает", RED)], w))
        if st.get("error"):
            out.append(line([(" сборщик: " + str(st["error"]), RED)], w))
        if view:
            sections = [now_block(view["now"], w, roomy)]
            if view.get("next"):
                sections.append(next_block(view["next"], w))
            if view.get("questions"):
                sections.append(ask_block(view["questions"], w))
            sections.append(machines_block(view["machines"], w, roomy))
            sections.append(news_block(view["news"], w, limit, view.get("news_src", "plain")))
            for i, sec in enumerate(sections):
                if i and roomy:
                    out.append(Text(""))
                out += sec
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
    argv = sys.argv[1:]
    once = "--once" in argv
    console = Console(force_terminal=True, color_system="standard", legacy_windows=False) if "--color" in argv else Console()
    if once:
        w = min(console.width if (sys.stdout.isatty() or "COLUMNS" in os.environ) else MAX_W, MAX_W)
        if "--status" in argv:
            st = json.loads(Path(argv[argv.index("--status") + 1]).read_text(encoding="utf-8"))
        else:
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
