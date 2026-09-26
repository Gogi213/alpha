#!/usr/bin/env python3
"""Тест чтения ночи (T-19, 26.09): ночь без старой базы (`OLD_BASE=0`, В-108) без строк `verdicts.csv`
читается как исправная, а настоящие поломки по-прежнему ловятся.

Запуск: `python -m pytest tools/compute/tests -q` (на Windows вывод кириллицы — PYTHONIOENCODING=utf-8).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "nightly-read.py"
_spec = importlib.util.spec_from_file_location("nightly_read", MODULE)
nr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nr)

OK_ALERTS = ["2026-09-26T02:18:22Z nightly-2026-09-26: ОК"]
OLD_BASE_LINE = "== 2026-09-26T02:05:00Z OLD_BASE=0 — старая база/tk/dl/E7/скальп пропущены намеренно (В-108)\n"


def test_no_verdicts_without_old_base_is_ok():
    assert nr.classify(OK_ALERTS, [], [], verdicts_expected=False) == ("ok", "none")


def test_no_verdicts_with_old_base_still_broken():
    # Прежнее поведение: старая база должна была написать вердикты — их нет, это поломка.
    assert nr.classify(OK_ALERTS, [], []) == ("broken", "no_verdicts")


def test_real_breakage_still_caught_without_old_base():
    cases = [
        (["nightly-2026-09-26: ночь пропущена"], [], "skipped"),
        (["nightly-2026-09-26: вердикт не посчитался"], [], "verdict_failed"),
        (["nightly-2026-09-26: ПРОВАЛ"], [], "other"),
        (OK_ALERTS, ["alpha-grid-nightly.service"], "unit_failed"),
    ]
    for alerts, units, cause in cases:
        assert nr.classify(alerts, [], units, verdicts_expected=False) == ("broken", cause), cause


def test_green_verdict_still_looked_at():
    rows = [["2026-09-26", "oos", "зелёный", "f", "10", "5", "1"]]
    assert nr.classify(OK_ALERTS, rows, [], verdicts_expected=False) == ("look", "green_verdict")


def test_old_base_skipped_reads_night_log(tmp_path):
    log = tmp_path / "nightly-2026-09-26.log"
    assert nr.old_base_skipped(str(log)) is False  # лога нет — вердикты ожидаются
    log.write_text("== 2026-09-26T02:00:00Z nightly start\n", encoding="utf-8")
    assert nr.old_base_skipped(str(log)) is False
    log.write_text(OLD_BASE_LINE, encoding="utf-8")
    assert nr.old_base_skipped(str(log)) is True
