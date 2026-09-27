#!/usr/bin/env python3
"""Три исходных читателя `_lib.py` (В-39), перенесённые сюда байт-в-байт при переходе на пакет
`_lib/` (T-21, `docs/findings/t21-metrics-canon-2026-09-27.md` §3 п.5): формулы не менялись, только
адрес файла. Публичный доступ — через `_lib/__init__.py` (`_lib.read_csv` и т.д.), этот файл сам не
грузится вызывающими скриптами.

`read_csv` — то же самое, что было продублировано в breakdown.py/equity-report.py/loss-atoms.py:
шапка-метаданные сетки на `#` пропускается, дальше обычный `csv.DictReader`. `read_regime_day` —
чтение study/regime/<сутки>.csv с фильтром «только минуты своих суток» (файл несёт окно 48 ч — с ним
и предыдущие сутки, regime.py/titration-points.py); без фильтра квантили режима считали бы часть
минут дважды (найдено при вводе v1 набора титрования 23.09).
"""
from __future__ import annotations

import csv
import datetime as dt
import glob
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


def fill_usd_by_form(run_dir: str, set_name: str) -> dict:
    """Деньги по формам **из самих сделок** (В-93: доллары — только с размером позиции): Σ net_bps/1e4 × qty ×
    entry_vwap по всем `rounds.csv` прогона `<run_dir>/<сутки>/<набор>/`. Размер — фактически исполненный
    (`qty` круга), как в счёте депозита и дашборде; прежние сводки печатали net × n / 10 — условную позицию
    $1000 (ревью 24.09: на $500-прогонах завышали доллары в ~3,6 раза). Нет файлов — пустой словарь."""
    out: dict = {}
    for f in sorted(glob.glob(os.path.join(run_dir, "20*", set_name, "rounds.csv"))):
        _, rows = read_csv(f)
        for r in rows:
            try:
                entry = float(r.get("entry_vwap") or 0) or float(r["entry_px"])
                usd = float(r["net_bps"]) / 1e4 * float(r["qty"]) * entry
            except (KeyError, ValueError):
                continue
            out[r["form"]] = out.get(r["form"], 0.0) + usd
    return out
