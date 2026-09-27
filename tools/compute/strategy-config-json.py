#!/usr/bin/env python3
"""Конфиг вариантов (В-128/В-129) — читает `docs/findings/strategy-config-2026-09-27.md`
(единственный источник правды, элемент протокола — НЕ дашборда) и пишет JSON: полный конфиг
«ключ -> значение» по группам (`## Полный конфиг — ключ → значение по группам`, подсекции `### <группа>`,
каждая — своя md-таблица с одной и той же шапкой из 9 вариантов) и таблица гипотез
(`## Гипотезы — отличия от главного`). Ничего не считает и не выдумывает — только разбирает markdown
построчно; если формат таблиц меняется несовместимо, разбор откажет явно (`AssertionError`), а не тихо
потеряет столбец/группу.

    python tools/compute/strategy-config-json.py \\
        --md docs/findings/strategy-config-2026-09-27.md \\
        --out data/titration-dashboard/strategy-config.json
"""
import argparse
import json
import re

BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
# Из ячейки шапки вида "Главный (`btc4h_trail`)" -> "btc4h_trail".
HEADER_KEY_RE = re.compile(r"`([a-z0-9_]+)`")

GROUPS_TITLE = "## Полный конфиг — ключ → значение по группам"
HYPOTHESES_TITLE = "## Гипотезы — отличия от главного"

SECTION_TITLES = [
    "## Ответ владельцу: где стоп на Г-85",
    GROUPS_TITLE,
    HYPOTHESES_TITLE,
    "## Примечание — выключатель BTC и потолок позиций (общее для всех вариантов)",
    "## Источники по столбцам",
    "## Что проверено, не найдено",
]


def parse_cell(raw):
    """Ячейка md-таблицы -> (текст без **, был ли в ней жирный фрагмент — «отличается от главного»)."""
    text = raw.strip()
    diff = bool(BOLD_RE.search(text))
    text = BOLD_RE.sub(r"\1", text)
    return text, diff


def split_row(line):
    # md-строка "| a | b | c |" -> ["a", "b", "c"]; экранированных '|' в этой таблице нет.
    parts = line.strip().split("|")
    assert parts[0].strip() == "" and parts[-1].strip() == "", f"строка таблицы не окаймлена '|': {line!r}"
    return parts[1:-1]


def read_table_at(lines, i):
    """Md-таблица, начинающаяся на строке `i` (должна начинаться с `| `) -> (header_cells, rows_raw, конец)."""
    assert lines[i].startswith("| "), f"строка {i} не похожа на начало md-таблицы: {lines[i]!r}"
    table_lines = []
    while i < len(lines) and lines[i].startswith("|"):
        table_lines.append(lines[i])
        i += 1
    assert len(table_lines) >= 3, f"в таблице меньше 3 строк (шапка+разделитель+хотя бы 1 ряд): {len(table_lines)}"
    header = [c.strip() for c in split_row(table_lines[0])]
    assert re.fullmatch(r":?-{2,}:?", table_lines[1].split("|")[1].strip()), \
        f"вторая строка таблицы не похожа на разделитель md: {table_lines[1]!r}"
    return header, table_lines[2:], i


def variant_keys_from_header(header):
    """Шапка группы (кроме первой ячейки 'параметр') -> [(колонка, variant_key)]."""
    out = []
    for col in header[1:]:
        m = HEADER_KEY_RE.search(col)
        assert m, f"в заголовке столбца нет `variant_key` в обратных кавычках: {col!r}"
        out.append((col, m.group(1)))
    return out


def find_section_start(lines, title):
    for i, l in enumerate(lines):
        if l.strip() == title:
            return i
    raise AssertionError(f"в md нет заголовка {title!r}")


def section_end(lines, start, level_prefix="## "):
    for i in range(start + 1, len(lines)):
        if lines[i].startswith(level_prefix):
            return i
    return len(lines)


def parse_groups(lines):
    start = find_section_start(lines, GROUPS_TITLE)
    end = section_end(lines, start, "## ")
    groups = []
    i = start + 1
    while i < end:
        if lines[i].startswith("### "):
            name = lines[i][4:].strip()
            j = i + 1
            while j < end and not lines[j].startswith("| "):
                assert not lines[j].startswith("### "), f"группа {name!r} без таблицы перед следующей ###"
                j += 1
            assert j < end, f"после '### {name}' не нашлась строка таблицы"
            header, rows_raw, j = read_table_at(lines, j)
            assert header[0].strip().lower() == "параметр", \
                f"первый столбец группы {name!r} обязан быть 'параметр', пришло {header[0]!r}"
            var_cols = variant_keys_from_header(header)
            rows = []
            for line in rows_raw:
                cells_raw = split_row(line)
                assert len(cells_raw) == len(header), (
                    f"группа {name!r}, строка {line!r}: {len(cells_raw)} ячеек, а в шапке {len(header)}"
                )
                param, _ = parse_cell(cells_raw[0])
                values = {}
                for (col, vkey), raw_cell in zip(var_cols, cells_raw[1:]):
                    text, diff = parse_cell(raw_cell)
                    values[vkey] = {"col": col, "text": text, "diff": diff}
                rows.append({"param": param, "values": values})
            groups.append({"name": name, "variant_keys": [k for _, k in var_cols], "rows": rows})
            i = j
        else:
            i += 1
    assert groups, "в разделе групп не нашлось ни одной '### <группа>' с таблицей"
    return groups


def parse_hypotheses(lines):
    start = find_section_start(lines, HYPOTHESES_TITLE)
    end = section_end(lines, start, "## ")
    i = start + 1
    while i < end and not lines[i].startswith("| "):
        i += 1
    assert i < end, "после заголовка гипотез не нашлась строка таблицы"
    header, rows_raw, _ = read_table_at(lines, i)
    assert len(header) == 4, f"таблица гипотез: ожидались 4 столбца, пришло {len(header)}: {header!r}"
    rows = []
    for line in rows_raw:
        cells_raw = split_row(line)
        assert len(cells_raw) == 4, f"строка гипотез {line!r} даёт {len(cells_raw)} ячеек, ожидалось 4"
        gid, change, status, link = (parse_cell(c)[0] for c in cells_raw)
        rows.append({"id": gid, "change": change, "status": status, "source": link})
    return header, rows


def extract_section(lines, title, next_titles):
    """Текст секции `## <title>` до следующего заголовка того же уровня из `next_titles`."""
    try:
        start = next(i for i, l in enumerate(lines) if l.strip() == title)
    except StopIteration:
        return None
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].strip() in next_titles:
            end = i
            break
    return "\n".join(lines[start + 1:end]).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    with open(a.md, encoding="utf-8") as f:
        md_text = f.read()
    lines = md_text.splitlines()

    groups = parse_groups(lines)
    hyp_header, hypotheses = parse_hypotheses(lines)
    answer = extract_section(lines, SECTION_TITLES[0], SECTION_TITLES[1:])
    protections_note = extract_section(lines, SECTION_TITLES[3], SECTION_TITLES[4:])
    sources = extract_section(lines, SECTION_TITLES[4], SECTION_TITLES[5:])
    not_found = extract_section(lines, SECTION_TITLES[5], [])

    out = {
        "generated_from": a.md,
        "groups": groups,
        "hypotheses_columns": hyp_header,
        "hypotheses": hypotheses,
        "answer_g85_stop": answer,
        "protections_note": protections_note,
        "sources_note": sources,
        "not_found_note": not_found,
    }
    with open(a.out, "w", encoding="utf-8", newline="") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    n_params = sum(len(g["rows"]) for g in groups)
    print(f"{a.out}: {len(groups)} групп, {n_params} параметров, {len(hypotheses)} гипотез")


if __name__ == "__main__":
    main()
