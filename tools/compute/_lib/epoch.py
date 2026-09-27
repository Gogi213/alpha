#!/usr/bin/env python3
"""Один разборщик `--epoch` (T-21 canon §2/§5, п.5 §3 «`_lib` модулем»). Ревизия T-21 нашла 4
синтаксиса (`t21-metrics-canon-2026-09-27.md` §1):

  А/канон  `имя=<дом>:<прогон>[,<прогон>…][@<с>..<по>]`  — portfolio-sim, exit-sim, loss-days,
                                                            family-titrate; хвост `@с..по` — доп.
                                                            фильтр дат поверх списка прогонов.
  Б        `имя=<дом>:<с>:<по>[:<период>]`                — titration-dashboard-data.
  В        `имя=<study dir>`                              — titration-read (без `:` вовсе).
  Г        shell `EPOCHS=` в titrate-forms.sh              — не разбирается здесь: это shell-цикл
                                                              вызовов, а не строка формата `--epoch`.

Различение Б от А: если после `<дом>:` идут РОВНО два или три токена и первые два выглядят как
даты `YYYY-MM-DD` — это Б; иначе — А (список прогонов, возможно с `,` и хвостом `@с..по`).

Повтор имени (canon §1: «повтор имени в А — portfolio-sim перезаписывает, остальные дописывают»;
Судья, условие 1 — проверены реальные вызовы, повторов нет, но правило нужно на будущее):
`parse_epochs` **дописывает** прогоны при повторе имени той же формы А; для форм Б/В повтор того
же имени — ошибка (нет смысла «дописывать» диапазон дат или каталог study)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class Epoch:
    name: str
    home: str
    runs: list[str] | None = None         # синтаксис А/канон
    start: str | None = None              # синтаксис Б
    end: str | None = None
    period: str | None = None
    study_dir: str | None = None          # синтаксис В
    range_start: str | None = None        # канон "@с..по" поверх прогонов А
    range_end: str | None = None


def parse_epoch(spec: str) -> Epoch:
    """Один `--epoch имя=...` → `Epoch`. Формат определяется по `spec` (см. модульный докстринг)."""
    if "=" not in spec:
        raise ValueError(f"--epoch без '=': {spec!r}")
    name, rest = spec.split("=", 1)
    if ":" not in rest:
        # В: имя=<study dir> — готовый каталог study, дома/прогона нет
        return Epoch(name=name, home=rest, study_dir=rest)
    parts = rest.split(":")
    home, tail = parts[0], parts[1:]
    if len(tail) in (2, 3) and _DATE_RE.match(tail[0]) and _DATE_RE.match(tail[1]):
        # Б: имя=дом:с:по[:период]
        return Epoch(name=name, home=home, start=tail[0], end=tail[1],
                     period=tail[2] if len(tail) == 3 else None)
    # А/канон: имя=дом:прогон[,прогон…][@с..по]
    runs_part = ":".join(tail)
    range_start = range_end = None
    if "@" in runs_part:
        runs_part, rng = runs_part.split("@", 1)
        if ".." in rng:
            range_start, range_end = rng.split("..", 1)
    return Epoch(name=name, home=home, runs=runs_part.split(","),
                 range_start=range_start, range_end=range_end)


def parse_epochs(specs) -> dict[str, Epoch]:
    """Список `--epoch` → `{имя: Epoch}`; повтор имени формы А ДОПИСЫВАЕТ `runs` (canon §1/§5,
    Судья условие 1). Повтор имени другой формы (Б/В) или смена формы при повторе — `ValueError`:
    у них дописывание не определено."""
    out: dict[str, Epoch] = {}
    for spec in specs:
        e = parse_epoch(spec)
        if e.name not in out:
            out[e.name] = e
            continue
        prev = out[e.name]
        if prev.runs is None or e.runs is None:
            raise ValueError(
                f"--epoch {e.name}: повтор имени поддержан только для формы А (список прогонов)")
        prev.runs = prev.runs + e.runs
        if e.range_start or e.range_end:
            prev.range_start, prev.range_end = e.range_start, e.range_end
    return out
