#!/usr/bin/env python3
"""Общие читатели счётных Python-скриптов (В-39: одна реализация вместо нескольких копий).

Загружать динамически по пути рядом со своим файлом (не `import _lib`: скрипты в `bin/` на счётной
машине лежат в плоском каталоге, а тесты грузят модуль напрямую по файлу — `sys.path` в обоих случаях
может не включать этот каталог):

    import importlib.util, os
    _spec = importlib.util.spec_from_file_location(
        "_lib", os.path.join(os.path.dirname(os.path.abspath(__file__)), "_lib.py"))
    _lib = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_lib)

`read_csv` — то же самое, что было продублировано в breakdown.py/equity-report.py/loss-atoms.py:
шапка-метаданные сетки на `#` пропускается, дальше обычный `csv.DictReader`. `read_regime_day` —
чтение study/regime/<сутки>.csv с фильтром «только минуты своих суток» (файл несёт окно 48 ч — с ним
и предыдущие сутки, regime.py/titration-points.py); без фильтра квантили режима считали бы часть
минут дважды (найдено при вводе v1 набора титрования 23.09).
"""
from __future__ import annotations

import csv
import datetime as dt
import os


def read_csv(path: str):
    """Заголовок + строки CSV; строки шапки на `#` (метаданные сетки) пропускаются.

    Отсутствие файла — как у голого `open()`: поднимает исключение (вызывающий решает сам,
    мягко это или нет — `loss-atoms.py` проверяет `os.path.exists` до вызова, остальные нет)."""
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        r = csv.DictReader(line for line in f if not line.startswith("#"))
        head = list(r.fieldnames or [])
        return head, list(r)


def read_regime_day(regime_dir: str, day: str) -> list[dict]:
    """Строки `<regime_dir>/<day>.csv` — только минуты СВОИХ суток (день считается по UTC)."""
    start = int(dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()) * 1000
    end = start + 86_400_000
    _, rows = read_csv(os.path.join(regime_dir, f"{day}.csv"))
    return [r for r in rows if start <= int(r["minute_ms"]) < end]
