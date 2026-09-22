#!/usr/bin/env python3
"""Страж предрегистрации (В-81; граница кода и модели — аудит дизайна 22.09 §1): проверка
наборов сетки **до** запуска прогона — то, чего не хватило 21.09 (E15: набор `a45-bid:age=2700,
side=bid,flow=100` объединил обе семьи флоров и занял имя ночного набора; вердикт «мало данных»
оказался свойством набора, а не стратегии — аудит этапа F §8 Ф1).

R1 (обе семьи флоров в одном наборе) и R2 (имя ночного набора с другими ключами) — правила,
которые код вычисляет точно, поэтому их проверяет **код**, с жёстким отказом и без ключа
TypeSafe. Раньше код считал эти факты и отдавал модели пересказать, а решение принималось по её
вероятности; без ключа проверки не было вовсе (прогон шёл) — это и была подмена правила мнением.
Модель (Jev) спрашивается только о R3 — семантике свободного текста строки предрегистрации
(«у каждого числа-параметра есть ссылка на В-## или замер», «названа ли гипотеза»); её ответ —
мнение с версией модели, отказ по нему — только при `--strict-r3`.

    python tools/compute/prereg-guard.py --sets "a45-bid:age=2700,side=bid"         --nightly tools/compute/nightly-grid.sh [--prereg "<строка runs.csv>"] [--json]

Выход 0 — допустимо; 1 — отказ по R1/R2 (или по R3 при `--strict-r3`); 2 — R1/R2 пройдены, но R3
запрошен и не проверен (нет ключа/сети) — причина печатается, решение за вызывающим.
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


def rule_check(name: str, keys: dict[str, str], nightly: dict[str, dict[str, str]]) -> list[str]:
    """R1/R2 кодом: список нарушений (пусто — набор допустим)."""
    bad = []
    if any(k in keys for k in AGE_KEYS) and any(k in keys for k in FLOW_KEYS):
        bad.append("R1: обе семьи флоров (возраст и сила ×поток) — набор пуст по построению")
    night = nightly.get(name)
    if night is not None and night != keys:
        bad.append(f"R2: имя ночного набора {name} с другими ключами (в ночи {json.dumps(night, ensure_ascii=False)})")
    return bad


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
    ap.add_argument("--prereg", default=None, help="строка предрегистрации для проверки R3 (модель)")
    ap.add_argument("--json", action="store_true", help="печать полного ответа")
    ap.add_argument("--strict", action="store_true", help="R3 не проверен (нет ключа/сети) — отказ (1), не 2")
    ap.add_argument("--strict-r3", action="store_true", help="отказ, если модель считает R3 нарушенным (p < 0.5)")
    a = ap.parse_args()

    nightly = nightly_sets(a.nightly)
    refused = False
    report: dict = {"sets": []}
    for spec in a.sets:
        name, keys = parse_set(spec)
        bad = rule_check(name, keys, nightly)
        refused |= bool(bad)
        report["sets"].append({"set": spec, "violations": bad})
        print(f"{'ОТКАЗ' if bad else 'ок':5} {spec}: " + ("; ".join(bad) if bad else "R1/R2 соблюдены (проверка кодом)"))
    if refused:
        print("prereg-guard: ОТКАЗ — прогон не запускать (R1/R2, код)")
        return 1
    if a.prereg is None:
        print("prereg-guard: допустимо (R1/R2 кодом; R3 не запрашивался)")
        return 0

    try:
        j = Judge()
        prereg = judge_prereg(j, a.prereg)
    except (MissingKey, RuntimeError) as e:
        print(f"prereg-guard: R1/R2 ок; R3 НЕ ПРОВЕРЕН — {e}")
        return 1 if a.strict else 2
    ns = prereg["numbers_sourced"]["noul"]
    hn = prereg["hypothesis_named"]["noul"]
    r3_bad = ns < 0.5
    print(
        f"{'ОТКАЗ' if (r3_bad and a.strict_r3) else ('СМОТРЕТЬ' if r3_bad else 'ок'):8} prereg — мнение модели "
        f"{j.model_version}: числа со ссылками p={ns:.2f}, гипотеза названа p={hn:.2f}"
    )
    report["prereg"] = prereg
    if a.json:
        report["usage"] = j.usage
        print(json.dumps(report, ensure_ascii=False, indent=1))
    if r3_bad and a.strict_r3:
        print("prereg-guard: ОТКАЗ по R3 (--strict-r3)")
        return 1
    print(f"prereg-guard: допустимо; TypeSafe {j.usage['requests']} запросов, {j.usage['input_tokens']}/{j.usage['output_tokens']} токенов")
    return 0


if __name__ == "__main__":
    sys.exit(main())
