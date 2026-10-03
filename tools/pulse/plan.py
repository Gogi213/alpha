#!/usr/bin/env python3
"""План шагов задачи для табло v2 (`tools/pulse/VIEW2.md`): исполнитель пишет шаги по-людски, табло их рисует.

    plan.py set TK-044 --title "Проверка данных на порчу" --flow "код пишет:pc,запускает и проверяет:vps" \\
        --for "данные дней:vps" --step "правила проверки|инженер|pc|код для VPS" --step "сборка и тесты|автомат|vps|запуск"
    plan.py step TK-044 2 run [--detail "231 из 492 монето-месяцев"]     # run|done|wait|bad|todo; started/finished — сами
    plan.py show TK-044

Шаг: `название|кто|где|для чего` (кто: инженер, исследователь, судья, автомат, вы, CEO; где: pc vps calc col you).
`set` поверх существующего плана сохраняет состояние шагов с тем же названием. Файл — `.claude/pulse/plans/<TK>.json`.
Время — GMT+4. Ход шага на сервере: `alpha-progress … step_n` (номер шага) — табло берёт процент оттуда.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pulsedata as P  # noqa: E402

MARK = {"done": "✓", "run": "▶", "wait": "⏸", "bad": "✗", "todo": "·"}


def plan_path(pid: str) -> Path:
    return P.PLANS_DIR / f"{pid}.json"


def parse_place(s: str, what: str) -> dict:
    """«текст:on» → {"text", "on"}; без/с неизвестным on — on=None (только для `--for`)."""
    text, _, on = s.rpartition(":")
    if text and on.strip() in P.VALID_ON:
        return {"text": text.strip(), "on": on.strip()}
    if what == "for":
        return {"text": s.strip(), "on": None}
    raise ValueError(f"--{what}: «{s}» — нужно «текст:{'|'.join(P.VALID_ON)}»")


def parse_step(spec: str) -> dict:
    parts = [x.strip() for x in spec.split("|")]
    if len(parts) < 3 or not parts[0]:
        raise ValueError(f"--step «{spec}»: нужно «название|кто|где[|для чего]»")
    title, who, on = parts[0], parts[1], parts[2]
    if on not in P.VALID_ON:
        raise ValueError(f"--step «{spec}»: где = {'|'.join(P.VALID_ON)}, не «{on}»")
    return {"title": title, "who": who, "on": on, "for": parts[3] if len(parts) > 3 else "", "state": "todo",
            "detail": None, "started_at": None, "finished_at": None, "question": None}


def cmd_set(a) -> int:
    pid = P.tk_id(a.id)
    try:
        flow = [parse_place(x, "flow") for x in a.flow.split(",") if x.strip()] if a.flow else []
        steps = [parse_step(s) for s in a.step]
        forp = parse_place(a.for_, "for") if a.for_ else {"text": "", "on": None}
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    if not steps:
        print("нужен хотя бы один --step", file=sys.stderr)
        return 2
    old = P.read_json(plan_path(pid)) or {}
    prev = {s.get("title"): s for s in old.get("steps", []) if isinstance(s, dict)}
    for s in steps:  # пересоставили план — состояние шагов с тем же названием не теряем
        o = prev.get(s["title"])
        if o:
            for k in ("state", "detail", "started_at", "finished_at", "question"):
                s[k] = o.get(k)
    plan = {"id": pid, "title": a.title, "flow": flow, "for": forp, "steps": steps, "updated": P.iso()}
    P.write_json(plan_path(pid), plan)
    print(f"{pid}: план из {len(steps)} шагов записан")
    return 0


def apply_state(s: dict, state: str, now: str, detail: str | None) -> None:
    """Состояние шага; started/finished ставятся сами."""
    s["state"] = state
    if state == "todo":
        s["started_at"] = s["finished_at"] = None
    elif state == "done":
        s["started_at"] = s.get("started_at") or now
        s["finished_at"] = now
    else:  # run / wait / bad
        s["started_at"] = s.get("started_at") or now
        s["finished_at"] = None
    if detail is not None:
        s["detail"] = detail or None


def set_step(pid: str, n: int, state: str, detail: str | None = None, **extra) -> dict:
    """Для ask.py тоже: поменять шаг n плана pid. Нет плана/шага — LookupError."""
    plan = P.read_json(plan_path(pid))
    if not isinstance(plan, dict) or not plan.get("steps"):
        raise LookupError(f"нет плана {pid} (plan.py set)")
    if not 1 <= n <= len(plan["steps"]):
        raise LookupError(f"{pid}: шага {n} нет (в плане {len(plan['steps'])})")
    s = plan["steps"][n - 1]
    now = P.iso()
    apply_state(s, state, now, detail)
    s.update(extra)
    plan["updated"] = now
    P.write_json(plan_path(pid), plan)
    return s


def cmd_step(a) -> int:
    pid = P.tk_id(a.id)
    try:
        s = set_step(pid, a.n, a.state, a.detail)
    except LookupError as e:
        print(e, file=sys.stderr)
        return 1
    print(f"{pid} шаг {a.n} «{s['title']}»: {s['state']}")
    return 0


def cmd_show(a) -> int:
    pid = P.tk_id(a.id)
    plan = P.read_json(plan_path(pid))
    if not isinstance(plan, dict):
        print(f"нет плана {pid}", file=sys.stderr)
        return 1
    fl = ", ".join(f"{x['text']} [{x['on']}]" for x in plan.get("flow", []))
    fr = plan.get("for") or {}
    print(f"{plan['id']} · {plan.get('title')}\n  как: {fl or '—'}\n  для: {fr.get('text') or '—'} [{fr.get('on') or '—'}]")
    for i, s in enumerate(plan["steps"], 1):
        when = f" {P.hhmm(s.get('started_at')) or ''}–{P.hhmm(s.get('finished_at')) or ''}" if s.get("started_at") else ""
        q = f" вопрос {s['question']}" if s.get("question") else ""
        d = f" · {s['detail']}" if s.get("detail") else ""
        print(f"  {i} {MARK.get(s['state'], '?')} {s['title']} · {s['who']} · {s['on']} · {s.get('for') or '—'}{d}{when}{q}")
    return 0


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="plan.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("set")
    s.add_argument("id")
    s.add_argument("--title", required=True)
    s.add_argument("--flow", default="")
    s.add_argument("--for", dest="for_", default="")
    s.add_argument("--step", action="append", default=[])
    s.set_defaults(func=cmd_set)
    s = sub.add_parser("step")
    s.add_argument("id")
    s.add_argument("n", type=int)
    s.add_argument("state", choices=P.STEP_STATES)
    s.add_argument("--detail", default=None)
    s.set_defaults(func=cmd_step)
    s = sub.add_parser("show")
    s.add_argument("id")
    s.set_defaults(func=cmd_show)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
