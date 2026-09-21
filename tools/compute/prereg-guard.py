#!/usr/bin/env python3
"""Страж предрегистрации (В-81, TypeSafe в мета-контуре): суждение о наборах сетки
**до** запуска прогона — то, чего не хватило 21.09 (E15: набор `a45-bid:age=2700,
side=bid,flow=100` объединил обе семьи флоров и занял имя ночного набора; вердикт
«мало данных» оказался свойством набора, а не стратегии — аудит §8 Ф1).

Код собирает **факты** (ключи набора, определение того же имени в ночи, ссылки на
В-## в строке предрегистрации), TypeSafe даёт **суждение** по правилам проекта,
страж отказывает при нарушении. Ключ — `TYPESAFE_API_KEY` из окружения.

    python tools/compute/prereg-guard.py --sets "a45-bid:age=2700,side=bid" \
        --nightly tools/compute/nightly-grid.sh [--prereg "<строка runs.csv>"] [--json]

Выход 0 — наборы допустимы; 1 — отказ (причины в stdout); 2 — страж не смог
спросить (нет ключа/сети): запуск **не** блокируется молча, причина печатается,
решение — за вызывающим (`--strict` превращает 2 в 1).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
# Клиент лежит в tools/typesafe/ (репо) или рядом (bin/ на счётной машине).
sys.path[:0] = [os.path.join(_HERE, "..", "typesafe"), _HERE]
from judge import Judge, MissingKey  # noqa: E402

# Консоль Windows по умолчанию не UTF-8 — печать русского текста ломается.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

RULES = (
    "Правила проекта alpha для наборов сетки (docs/findings/floors-balance-2026-09-19.md §1, "
    "CLAUDE.md, EXPERIMENTS.md):\n"
    "R1. Есть две семьи флоров: «возраст» (ключ age=<с>: стена стоит не меньше N секунд) и "
    "«сила ×поток» (ключ flow=<%>: плотность к обороту монеты за час). Они НЕ пересекаются: "
    "сила ≥ 100 % И возраст ≥ 15 мин дают ~3 касания в сутки на весь пул. Набор, в котором заданы "
    "оба ключа сразу, пуст по построению и не проверяет стратегию — такой прогон невалиден.\n"
    "R2. Имена наборов ночной сетки фиксированы (nightly-grid.sh). Использовать имя ночного "
    "набора с другими ключами — коллизия имени: результаты под одним именем несравнимы.\n"
    "R3. Каждый числовой ПАРАМЕТР формы или фильтра в предрегистрации (пороги eat/gone, prob:<n>, "
    "полосы bps, ttl, стопы/тейки, дедлайны, ноги лестницы, порог номинала, полоса D) — только из "
    "решения владельца (В-##) или замера (M##/E##); параметр без ссылки — изобретённое число. "
    "Счётчики и координаты (число форм, число монет, даты суток, пути каталогов, RTT-константы с "
    "пометкой В-68) параметрами не считаются.\n"
)

AGE_KEYS = ("age",)
FLOW_KEYS = ("flow",)


def parse_set(spec: str) -> tuple[str, dict[str, str]]:
    name, _, rest = spec.partition(":")
    keys: dict[str, str] = {}
    for kv in filter(None, rest.split(",")):
        k, _, v = kv.partition("=")
        keys[k.strip()] = v.strip()
    return name.strip(), keys


def nightly_sets(path: str) -> dict[str, dict[str, str]]:
    """Имена наборов ночи и их ключи из nightly-grid.sh (все `<имя>:<k=v,…>` в тексте)."""
    out: dict[str, dict[str, str]] = {}
    if not path or not os.path.exists(path):
        return out
    text = open(path, encoding="utf-8").read()
    for m in re.finditer(r"(?<![\w/.-])([A-Za-z][\w.-]*):((?:[a-z_0-9]+=[^\s,\\\"']+)(?:,[a-z_0-9]+=[^\s,\\\"']+)*)", text):
        name, keys = parse_set(m.group(0))
        out.setdefault(name, keys)
    return out


def facts_for(name: str, keys: dict[str, str], nightly: dict[str, dict[str, str]]) -> str:
    has_age = any(k in keys for k in AGE_KEYS)
    has_flow = any(k in keys for k in FLOW_KEYS)
    night = nightly.get(name)
    lines = [
        f"Набор: {name}",
        f"Ключи набора: {json.dumps(keys, ensure_ascii=False)}",
        f"Ключ семьи «возраст» задан: {'да' if has_age else 'нет'}",
        f"Ключ семьи «сила ×поток» задан: {'да' if has_flow else 'нет'}",
    ]
    if night is None:
        lines.append("В ночной сетке набора с таким именем нет.")
    else:
        same = night == keys
        lines.append(
            f"В ночной сетке имя {name} определено как {json.dumps(night, ensure_ascii=False)} — "
            f"{'те же ключи' if same else 'ДРУГИЕ ключи'}."
        )
    return "\n".join(lines)


def judge_sets(j: Judge, specs: list[str], nightly: dict[str, dict[str, str]]) -> list[dict]:
    results = []
    for spec in specs:
        name, keys = parse_set(spec)
        state = RULES + "\nФакты о проверяемом наборе:\n" + facts_for(name, keys, nightly)
        ans = j.ask(
            state,
            {
                "both_families": Judge.noul(
                    "Судя по фактам и правилу R1, объединяет ли этот набор обе семьи флоров "
                    "(и возраст, и силу) — то есть пуст ли он по построению?"
                ),
                "name_collision": Judge.noul(
                    "Судя по фактам и правилу R2, занимает ли набор имя ночного набора с другими ключами?"
                ),
                "verdict": Judge.choice(
                    "Допустим ли набор для прогона по правилам R1–R2?",
                    {
                        "valid": "Набор не нарушает R1 и R2: одна семья флоров, имя либо новое, либо совпадает с ночным по ключам",
                        "invalid": "Набор нарушает R1 (обе семьи) или R2 (имя ночного набора с другими ключами)",
                        "unclear": "По фактам нельзя решить",
                    },
                ),
            },
        )
        results.append({"set": spec, "name": name, "keys": keys, "answers": ans})
    return results


def judge_prereg(j: Judge, row: str) -> dict:
    state = RULES + "\nСтрока предрегистрации (runs.csv):\n" + row
    return j.ask(
        state,
        {
            "numbers_sourced": Judge.noul(
                "По правилу R3: у каждого числового ПАРАМЕТРА формы/фильтра в строке (eat/gone, prob, "
                "полосы bps, ttl, стоп/тейк, дедлайн, ноги, номинал, D) есть ссылка на решение В-## "
                "или замер M##/E##? Число форм, даты и пути не в счёт."
            ),
            "hypothesis_named": Judge.noul(
                "Названа ли в строке одна главная гипотеза (а не только перечисление осей)?"
            ),
        },
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sets", nargs="+", required=True, help="наборы `<имя>:<k=v,…>`")
    ap.add_argument("--nightly", default="tools/compute/nightly-grid.sh", help="скрипт ночи — источник имён")
    ap.add_argument("--prereg", default=None, help="строка предрегистрации для проверки R3")
    ap.add_argument("--json", action="store_true", help="печать полного ответа")
    ap.add_argument("--strict", action="store_true", help="недоступность стража — отказ (выход 1), не 2")
    a = ap.parse_args()

    try:
        j = Judge()
    except MissingKey as e:
        print(f"prereg-guard: {e}")
        return 1 if a.strict else 2

    nightly = nightly_sets(a.nightly)
    try:
        results = judge_sets(j, a.sets, nightly)
        prereg = judge_prereg(j, a.prereg) if a.prereg else None
    except RuntimeError as e:
        print(f"prereg-guard: {e}")
        return 1 if a.strict else 2

    refused = False
    for r in results:
        ans = r["answers"]
        both = ans["both_families"]["noul"]
        coll = ans["name_collision"]["noul"]
        verdict = ans["verdict"]["choice"]
        conf = ans["verdict"]["confidence"]
        bad = verdict == "invalid" or both >= 0.5 or coll >= 0.5
        refused |= bad
        flag = "ОТКАЗ" if bad else "ок"
        print(
            f"{flag:5} {r['set']}: обе семьи p={both:.2f}, коллизия имени p={coll:.2f}, "
            f"вердикт={verdict} ({conf:.2f})"
        )
    if prereg is not None:
        ns = prereg["numbers_sourced"]["noul"]
        hn = prereg["hypothesis_named"]["noul"]
        bad = ns < 0.5
        refused |= bad
        print(
            f"{'ОТКАЗ' if bad else 'ок':5} prereg: числа со ссылками p={ns:.2f}, "
            f"гипотеза названа p={hn:.2f}"
        )
    if a.json:
        print(json.dumps({"sets": results, "prereg": prereg, "usage": j.usage}, ensure_ascii=False, indent=1))
    print(
        f"prereg-guard: {'ОТКАЗ — прогон не запускать' if refused else 'допустимо'}; "
        f"TypeSafe {j.usage['requests']} запросов, {j.usage['input_tokens']}/{j.usage['output_tokens']} токенов"
    )
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(main())
