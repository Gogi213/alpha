#!/usr/bin/env python3
"""`_lib` — общие читатели и канонические определения счётных Python-скриптов (В-39; пакетом — T-21,
`docs/findings/t21-metrics-canon-2026-09-27.md` §3 п.5 «`_lib` модулем»). Было одним файлом `_lib.py`
— три функции ниже (не изменились, перенесены в `legacy.py`); теперь пакет из нескольких модулей:

  `_lib.trade`    — каноническая строка сделки (`Trade`, `from_row`) — T-21 §5/условие 3.
  `_lib.epoch`    — один разборщик `--epoch` (`parse_epoch`, `parse_epochs`) — T-21 §2.
  `_lib.metrics`  — день/месяц/Шарп/просадка-по-закрытиям — T-21 §2/§5 (решения Судьи).
  `_lib.portfolio`— ПОКА заглушка (правила портфеля — отдельный шаг после T-21).

Загружать по-прежнему динамически по пути (не `import _lib`: скрипты в `bin/` на счётной машине
лежат в плоском каталоге, а тесты грузят модуль напрямую по файлу — `sys.path` в обоих случаях
может не включать этот каталог) — АДРЕС ФАЙЛА У ВЫЗЫВАЮЩИХ МЕНЯЕТСЯ с `_lib.py` на `_lib/__init__.py`,
остальной код вызывающих не трогается (`spec_from_file_location` сам распознаёт `__init__.py` и
проставляет `submodule_search_locations` — проверено; проблема со relative-import внутри пакета не
возникает, т.к. этот файл сам грузит `legacy.py` по пути, а не через `from . import legacy`):

    import importlib.util, os
    _spec = importlib.util.spec_from_file_location(
        "_lib", os.path.join(os.path.dirname(os.path.abspath(__file__)), "_lib", "__init__.py"))
    _lib = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_lib)

`_lib.trade`/`_lib.epoch`/`_lib.metrics`/`_lib.portfolio` — самостоятельные файлы без взаимных
импортов; кто хочет именно их, грузит `_lib/trade.py` и т.п. тем же способом напрямую (так делают
тесты `test_lib_trade.py`/`test_lib_epoch.py`/`test_lib_metrics.py`), либо берёт готовые атрибуты
`_lib.Trade`/`_lib.from_row`/… — они реэкспортированы ниже.
"""
from __future__ import annotations

import importlib.util as _ilu
import os as _os
import sys as _sys

_here = _os.path.dirname(_os.path.abspath(__file__))


def _load(name: str, filename: str):
    """`sys.modules` регистрация ДО `exec_module` — иначе `dataclasses` (см. `trade.py::Trade`,
    отложенные аннотации `from __future__ import annotations`) не резолвит `cls.__module__` и
    падает `AttributeError` при обращении к полю с `X | None` (найдено при первом прогоне тестов
    после сборки пакета)."""
    qualname = f"_lib.{name}"
    spec = _ilu.spec_from_file_location(qualname, _os.path.join(_here, filename))
    mod = _ilu.module_from_spec(spec)
    _sys.modules[qualname] = mod
    spec.loader.exec_module(mod)
    return mod


_legacy = _load("legacy", "legacy.py")
read_csv = _legacy.read_csv
read_regime_day = _legacy.read_regime_day
fill_usd_by_form = _legacy.fill_usd_by_form

_trade = _load("trade", "trade.py")
Trade = _trade.Trade
from_row = _trade.from_row
day_utc_of = _trade.day_utc_of

_epoch = _load("epoch", "epoch.py")
Epoch = _epoch.Epoch
parse_epoch = _epoch.parse_epoch
parse_epochs = _epoch.parse_epochs

_metrics = _load("metrics", "metrics.py")
MONTH_WINDOWS = _metrics.MONTH_WINDOWS
day_of_exit = _metrics.day_of_exit
month_of_signal = _metrics.month_of_signal
daily_pnl = _metrics.daily_pnl
sharpe_daily = _metrics.sharpe_daily
drawdown_closed = _metrics.drawdown_closed

_portfolio = _load("portfolio", "portfolio.py")
DROP_DEFAULT = _portfolio.DROP_DEFAULT
