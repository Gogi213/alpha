#!/usr/bin/env python3
"""Сверка план ↔ память ↔ CLAUDE.md (В-81 п. 5, TypeSafe): расходятся ли четыре источника
состояния — `.memory/index.md` §Состояние (верхний блок), `.memory/log.md` (последние записи),
`docs/plan/dev-plan-*.md` §0а (таблица задач) и CLAUDE.md (последний абзац состояния) — по пяти
вопросам: следующий шаг, число тестов, что закрыто/открыто, HEAD-коммит, статус прогона.
Это то, чего не хватило 21.09 (Ф5: план и память говорили «ждёт чисел», лог — «прогон сделан»).

    python tools/compute/plan-log-check.py [--plan docs/plan/dev-plan-2026-09-20.md] [--json]

Выход 0 — согласованы; 1 — расходятся (где — в stdout); 2 — суждение недоступно.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(_HERE, "..", "typesafe"), _HERE]
from judge import Judge, MissingKey  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def index_state(text: str, limit: int = 4000) -> str:
    m = re.search(r"^## Состояние \(.*?$", text, re.M)
    if not m:
        return text[:limit]
    rest = text[m.start():]
    nxt = re.search(r"^## Состояние \(", rest[1:], re.M)
    block = rest[: nxt.start() + 1] if nxt else rest
    return block[:limit]


def log_tail(text: str, n: int = 6, limit: int = 4000) -> str:
    entries = [l for l in text.splitlines() if l.startswith("- 2026-") or l.startswith("## [")]
    return "\n".join(entries[-n:])[:limit]


def plan_table(text: str, limit: int = 4000) -> str:
    rows = [l for l in text.splitlines() if l.startswith("| F") or l.startswith("| **F") or l.startswith("| **блокер")]
    return "\n".join(rows)[:limit]


def claude_state(text: str, limit: int = 3000) -> str:
    paras = [p for p in text.split("\n\n") if p.startswith("**2") or p.startswith("**21.09") or p.startswith("**20.09")]
    return "\n\n".join(paras[-2:])[:limit]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", default="docs/plan/dev-plan-2026-09-20.md")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    try:
        j = Judge()
    except MissingKey as e:
        print(f"plan-log-check: {e}")
        return 2
    state = "\n\n".join(
        [
            "Четыре источника состояния проекта alpha. Нужно найти расхождения по существу (следующий шаг, "
            "что закрыто/открыто, число тестов, статус прогона/ночи), а не по формулировкам.",
            "=== .memory/index.md, верхний блок §Состояние ===\n" + index_state(read(".memory/index.md")),
            "=== .memory/log.md, последние записи ===\n" + log_tail(read(".memory/log.md")),
            f"=== {a.plan} §0а, строки задач ===\n" + plan_table(read(a.plan)),
            "=== CLAUDE.md, последние абзацы состояния ===\n" + claude_state(read("CLAUDE.md")),
        ]
    )
    qs = {
        "next_step_agrees": Judge.noul("Все четыре источника называют один и тот же следующий шаг (или не противоречат друг другу о нём)?"),
        "closed_open_agrees": Judge.noul("Список закрытых/открытых задач согласован между планом, памятью и CLAUDE.md (нет задачи, которая в одном месте «закрыта», в другом «не начата»)?"),
        "tests_agree": Judge.noul("Число тестов (passed) одинаково там, где оно названо?"),
        "run_status_agrees": Judge.noul("Статус текущего прогона/ночи (идёт, упал, невалиден, сделан) не противоречит между источниками?"),
        "worst": Judge.choice(
            "Самое существенное расхождение",
            {
                "none": "расхождений по существу нет",
                "next_step": "разный следующий шаг",
                "task_status": "статус задачи противоречив",
                "numbers": "разные числа (тесты, круги, HEAD)",
                "run_status": "статус прогона/ночи противоречив",
                "stale_source": "один из источников явно устарел целиком (описывает прошлое состояние как текущее)",
            },
        ),
    }
    try:
        ans = j.ask(state, qs)
    except RuntimeError as e:
        print(f"plan-log-check: {e}")
        return 2
    bad = [k for k in ("next_step_agrees", "closed_open_agrees", "tests_agree", "run_status_agrees") if ans[k]["noul"] < 0.5]
    worst = ans["worst"]["choice"]
    print(
        "plan-log-check: "
        + ("СОГЛАСОВАНО" if not bad and worst == "none" else "РАСХОДЯТСЯ")
        + " — шаг {:.2f}, задачи {:.2f}, тесты {:.2f}, прогон {:.2f}; главное: {} ({:.2f})".format(
            ans["next_step_agrees"]["noul"], ans["closed_open_agrees"]["noul"], ans["tests_agree"]["noul"],
            ans["run_status_agrees"]["noul"], worst, ans["worst"]["confidence"],
        )
    )
    if a.json:
        print(json.dumps(ans, ensure_ascii=False, indent=1))
    return 1 if bad or worst != "none" else 0


if __name__ == "__main__":
    sys.exit(main())
