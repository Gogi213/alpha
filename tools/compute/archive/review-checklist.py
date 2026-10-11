#!/usr/bin/env python3
"""Первый проход ревью диффа (В-81 п. 4, TypeSafe): по каждому изменённому файлу — вероятность
нарушения правил проекта (семь запретов горячего пути, изобретённые числа, артефакты не по адресу,
тесты без проверки заявленного) и вес. Заменяет первый проход субагента (≈180k токенов) на
десяток запросов по $0; второй проход человеком/моделью — только по файлам с «важно/блокер».

    python tools/compute/review-checklist.py <от>..<до> [--max-chars 6000] [--json]

Выход 0 — блокеров/важного нет; 1 — есть (таблица в stdout); 2 — суждение недоступно.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(_HERE, "..", "typesafe"), _HERE]
from judge import Judge, MissingKey  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

RULES = (
    "Правила проекта alpha (CLAUDE.md, interfaces.md):\n"
    "H1 горячий путь (src/lob/strategy.rs on_event и всё, что он зовёт; src/lob/levels.rs на событие; "
    "src/book, src/feed, src/bybit/conn): ноль аллокаций на событие после прогрева (Vec::new/push с ростом, "
    "String, format!, Box, clone коллекций); время только через трейт Clock / переданные метки, не Instant/SystemTime; "
    "без async с захватом состояния; без REST в событийном цикле; без f64 для цены/размера (цены — целые тики, "
    "размер — лоты e9); без BTreeMap/HashMap на пути события.\n"
    "Оговорки к H1: количества и цены заявок крейта hftbacktest (Order.qty/leaves_qty/exec_price) — f64 по контракту крейта, "
    "это не нарушение; поиск заявки по id в карте крейта (bot.orders().get) — существующий путь, не нарушение; "
    "нарушение — СВОИ новые f64 для цен/размеров в тиках/лотах, свои карты, свои аллокации на событие.\n"
    "N1 изобретённое число запрещено: любой порог/константа в КОДЕ ПРОДУКТА — измерение (M##) или решение владельца (В-##) "
    "с ссылкой в комментарии. К тестам, фикстурам и скриптам разбора (константы сценария, задержки в тестах) N1 не относится.\n"
    "A1 артефакты пишутся по адресу из таблицы CLAUDE.md (docs/findings/<тема>-<дата>.md, data/, runs.csv), не «рядом».\n"
    "T1 тест обязан проверять заявленное в его докстринге; тест, который проходит и без правки, — не тест.\n"
    "S1 логика и структура: функция > ~150 строк или вложенность > 6 — смелл; дублирование существующей функции — смелл.\n"
    "Комментарии в коде — по-русски, ссылки на решения В-##/задачи плана."
)

HOT = ("src/lob/strategy.rs", "src/lob/levels.rs", "src/book/", "src/feed/", "src/bybit/conn")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("range")
    ap.add_argument("--max-chars", type=int, default=6000)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    files = [l for l in git("diff", "--name-only", a.range, "--", "src", "tools", "vendor").splitlines() if l.strip()]
    if not files:
        print("review-checklist: в диапазоне нет изменений src/tools/vendor")
        return 0
    try:
        j = Judge()
    except MissingKey as e:
        print(f"review-checklist: {e}")
        return 2
    rows = []
    flagged = 0
    for f in files:
        diff = git("diff", a.range, "--", f)
        if len(diff) > a.max_chars:
            diff = diff[: a.max_chars] + f"\n… (обрезано, всего {len(diff)} символов)"
        hot = any(f.startswith(h) for h in HOT)
        state = RULES + f"\n\nФайл: {f} (горячий путь: {'да' if hot else 'нет'})\nДифф:\n" + diff
        try:
            ans = j.ask(
                state,
                {
                    "hot_path_violation": Judge.noul("В диффе есть нарушение H1 (аллокация на событие, часы напрямую, f64 по цене/размеру, карта на пути события) в коде горячего пути?"),
                    "invented_number": Judge.noul("В диффе появилось число-порог/константа без ссылки на измерение или В-## (N1)?"),
                    "test_weak": Judge.noul("Есть тест, который не проверяет заявленное (T1): проходит и без правки, или проверяет через .any вместо точного счёта?"),
                    "severity": Judge.choice(
                        "Вес худшей находки в этом файле",
                        {
                            "none": "находок нет",
                            "smell": "стиль/структура/комментарий, поведение верно",
                            "important": "поведение или контракт под вопросом, чинить до боевого прогона",
                            "blocker": "явное нарушение H1/N1 или логическая ошибка, чинить до коммита",
                        },
                    ),
                },
            )
        except RuntimeError as e:
            print(f"review-checklist: {f}: {e}")
            continue
        sev = ans["severity"]["choice"]
        if sev in ("important", "blocker"):
            flagged += 1
        rows.append({"file": f, "hot": hot, "answers": ans})
        print(
            f"{sev:9} {f}: H1 {ans['hot_path_violation']['noul']:.2f}, число {ans['invented_number']['noul']:.2f}, "
            f"тест {ans['test_weak']['noul']:.2f} (концентрация {ans['severity']['confidence']:.2f})"
        )
    print(f"review-checklist: мнение модели {j.model_version} — подсказка ревьюеру, не вердикт; файлов {len(rows)}, важно/блокер {flagged}; TypeSafe {j.usage['requests']} запросов, {j.usage['input_tokens']}/{j.usage['output_tokens']} токенов")
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    return 1 if flagged else 0


if __name__ == "__main__":
    sys.exit(main())
