#!/usr/bin/env python3
"""Конфиг вариантов дашборда (В-128) — читает `docs/findings/strategy-config-2026-09-27.md`
(единственный источник правды, таблица под заголовком `## Таблица`) и пишет JSON для дашборда
(раздел «Конфиг вариантов», `--merge cfg=...` у `titration-dashboard-build.py`). Ничего не
считает и не выдумывает — только разбирает markdown-таблицу построчно; если формат таблицы
меняется несовместимо, разбор откажет явно (`AssertionError`), а не тихо потеряет столбец.

    python tools/compute/strategy-config-json.py \\
        --md docs/findings/strategy-config-2026-09-27.md \\
        --out data/titration-dashboard/strategy-config.json
"""
import argparse
import json
import re

# Ключ variant_key — какой переключатель варианта на дашборде подсвечивать этой строкой конфига
# (у Г-85 на дашборде одна кнопка `g85` = Г-85а — T-35; Г-85б без кнопки, только строка конфига).
VARIANT_KEY_FOR_ROW = {
    "btc4h_trail": "btc4h_trail", "btc4h_take": "btc4h_take", "cand": "cand", "nofilter": "nofilter",
    "g105": "g105", "g85a": "g85", "g85b": None, "g86": "g86", "g08": "g08",
}

BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def parse_cell(raw):
    """Ячейка md-таблицы -> (текст без **, был ли в ней жирный фрагмент — «отличается от главного»)."""
    text = raw.strip()
    diff = bool(BOLD_RE.search(text))
    text = BOLD_RE.sub(r"\1", text)
    return text, diff


def parse_table(md_text):
    lines = md_text.splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.strip() == "## Таблица")
    except StopIteration as exc:
        raise AssertionError("в md нет заголовка '## Таблица'") from exc
    table_lines = []
    i = start + 1
    while i < len(lines) and not lines[i].startswith("| "):
        i += 1
    assert i < len(lines), "после '## Таблица' не нашлась строка таблицы (начинается с '| ')"
    while i < len(lines) and lines[i].startswith("|"):
        table_lines.append(lines[i])
        i += 1
    assert len(table_lines) >= 3, f"в таблице меньше 3 строк (шапка+разделитель+хотя бы 1 ряд): {len(table_lines)}"

    def split_row(line):
        # md-строка "| a | b | c |" -> ["a", "b", "c"]; экранированных '|' в этой таблице нет.
        parts = line.strip().split("|")
        assert parts[0].strip() == "" and parts[-1].strip() == "", f"строка таблицы не окаймлена '|': {line!r}"
        return parts[1:-1]

    header = [c.strip() for c in split_row(table_lines[0])]
    assert re.fullmatch(r":?-{2,}:?", table_lines[1].split("|")[1].strip()), \
        f"вторая строка таблицы не похожа на разделитель md: {table_lines[1]!r}"
    assert header[0] == "ключ", f"первый столбец обязан быть 'ключ', пришло {header[0]!r}"

    rows = []
    for line in table_lines[2:]:
        cells_raw = split_row(line)
        assert len(cells_raw) == len(header), (
            f"строка {line!r} даёт {len(cells_raw)} ячеек, а в шапке {len(header)}"
        )
        cells = [parse_cell(c) for c in cells_raw]
        key = cells[0][0].strip("`").strip()
        row = {
            "key": key,
            "variant_key": VARIANT_KEY_FOR_ROW.get(key, key),
            "cells": [
                {"col": header[j], "text": text, "diff": diff}
                for j, (text, diff) in enumerate(cells)
            ],
        }
        rows.append(row)
    return header, rows


def extract_section(md_text, title, next_titles):
    """Текст секции `## <title>` до следующего заголовка того же уровня из `next_titles`."""
    lines = md_text.splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.strip() == f"## {title}")
    except StopIteration:
        return None
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("## ") and lines[i].strip()[3:] in next_titles:
            end = i
            break
    return "\n".join(lines[start + 1:end]).strip()


SECTION_TITLES = [
    "Ответ владельцу: где стоп на Г-85",
    "Таблица",
    "Примечание — выключатель BTC и потолок позиций (общее для всех строк)",
    "Источники по столбцам",
    "Что проверено, не найдено",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    with open(a.md, encoding="utf-8") as f:
        md_text = f.read()

    header, rows = parse_table(md_text)
    answer = extract_section(md_text, SECTION_TITLES[0], SECTION_TITLES[1:])
    protections_note = extract_section(md_text, SECTION_TITLES[2], SECTION_TITLES[3:])
    sources = extract_section(md_text, SECTION_TITLES[3], SECTION_TITLES[4:])
    not_found = extract_section(md_text, SECTION_TITLES[4], [])

    out = {
        "generated_from": a.md,
        "columns": header,
        "rows": rows,
        "answer_g85_stop": answer,
        "protections_note": protections_note,
        "sources_note": sources,
        "not_found_note": not_found,
    }
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"{a.out}: {len(rows)} строк, {len(header)} столбцов")


if __name__ == "__main__":
    main()
