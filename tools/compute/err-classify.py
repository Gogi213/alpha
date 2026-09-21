#!/usr/bin/env python3
"""Разбор ошибки прогона (В-81 п. 6, TypeSafe): хвост `.err`/лога → класс ошибки, известная ли
(по списку сигнатур проекта), что делать — одной строкой, вместо чтения лога человеком или моделью
с полным контекстом.

    python3 bin/err-classify.py b5/f10fix-D20-multi.err [--lines 40]

Выход 0 — ошибок нет; 1 — ошибка (класс в stdout); 2 — суждение недоступно (нет ключа/сети).
"""
from __future__ import annotations

import argparse
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

# Известные сигнатуры (docs/findings, COMMANDS.md): что означают и что делать.
KNOWN = {
    "invalid_order_status": (
        r"order status is invalid to proceed the request",
        "форк крейта PartialFillExchange (правки 20.09 и 22.09): заявка в терминальном статусе осталась в карте биржи; если бинарник старше cab355c — пересобрать; иначе новый путь — репро тестом",
    ),
    "oom": (r"Killed|MemoryMax|out of memory|OOM", "память: MemoryMax юнита или машина; снизить --threads/наборы, см. round-validation §6"),
    "cache_missing": (r"кэш подходов не годится|нет кэша|touches-from", "кэш касаний/подходов не на те сутки — корень дня study/root-<сутки>, см. COMMANDS §Ночная сетка"),
    "k1_marker": (r"verify-.*status|--allow-unverified|без маркера", "K1: нет маркера verify ok — сутки не сверены или маркеры не за те сутки (day_root, 22.09)"),
    "flag_refused": (r"требует --|обязателен|умолчания в коде нет|ожидается", "отказ по флагу: число не назначено — предрегистрация/В-##"),
    "disk_full": (r"No space left on device|ENOSPC", "диск полон — чистить study/*/старое, b5/, target"),
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path")
    ap.add_argument("--lines", type=int, default=40)
    a = ap.parse_args()
    if not os.path.exists(a.path):
        print(f"err-classify: нет файла {a.path}")
        return 2
    with open(a.path, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    tail = "\n".join(lines[-a.lines:])
    err_lines = [l for l in lines if re.search(r"Error|error\[|panicked|ОТКАЗ|отказ|Killed", l)]
    if not err_lines:
        print(f"err-classify: {a.path}: строк с ошибкой нет ({len(lines)} строк)")
        return 0
    matched = [k for k, (rx, _) in KNOWN.items() if re.search(rx, tail)]
    try:
        j = Judge()
    except MissingKey as e:
        print(f"err-classify: {e}; сигнатуры по regex: {matched or 'нет'}; последняя ошибка: {err_lines[-1][:200]}")
        return 2
    known_text = "\n".join(f"- {k}: {rx} → {act}" for k, (rx, act) in KNOWN.items())
    state = (
        "Проект alpha, лог прогона бэктеста (lob bounce-grid / touches / verdict). Известные классы ошибок и что делать:\n"
        + known_text
        + f"\n\nСовпадения по сигнатурам (regex): {matched or 'нет'}\n\nХвост лога:\n"
        + tail
    )
    try:
        ans = j.ask(
            state,
            {
                "cls": Judge.choice(
                    "Класс ошибки",
                    {**{k: act for k, (_, act) in KNOWN.items()}, "new": "новый вид ошибки, сигнатур нет — нужен разбор", "none": "ошибок по сути нет (шум, предупреждение)"},
                ),
                "fatal": Judge.noul("Прогон остановился на этой ошибке (артефакты неполные), а не продолжил работу?"),
                "same_as_known": Judge.noul("Это та же ошибка, что уже описана в известных классах (не новый путь)?"),
            },
        )
    except RuntimeError as e:
        print(f"err-classify: {e}")
        return 2
    c = ans["cls"]["choice"]
    act = KNOWN.get(c, (None, "разбор человеком/моделью"))[1]
    print(
        f"{os.path.basename(a.path)}: класс {c} ({ans['cls']['confidence']:.2f}); прогон остановлен p={ans['fatal']['noul']:.2f}; "
        f"известная p={ans['same_as_known']['noul']:.2f}; действие: {act}; последняя строка: {err_lines[-1][:160]}"
    )
    return 1 if c != "none" else 0


if __name__ == "__main__":
    sys.exit(main())
