#!/usr/bin/env python3
"""`_lib.epoch` (T-21): один разборщик `--epoch` — три реальных синтаксиса (А/канон, Б, В) и
повтор имени формы А дописывает прогоны.

Запуск: `python -m pytest tools/compute/tests -q`.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]


def load_epoch():
    spec = importlib.util.spec_from_file_location("_lib.epoch", HERE / "_lib" / "epoch.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_lib.epoch"] = mod
    spec.loader.exec_module(mod)
    return mod


epoch = load_epoch()


def test_syntax_a_canon_single_run():
    e = epoch.parse_epoch("история=epochs/e-archive:b5/titrc-u500-trail")
    assert e.name == "история"
    assert e.home == "epochs/e-archive"
    assert e.runs == ["b5/titrc-u500-trail"]
    assert e.start is None and e.study_dir is None


def test_syntax_a_multiple_runs_and_range_suffix():
    e = epoch.parse_epoch("сентябрь=.:b5/titrc-u500-trail,b5/titrc-v2@2026-09-01..2026-09-23")
    assert e.runs == ["b5/titrc-u500-trail", "b5/titrc-v2"]
    assert e.range_start == "2026-09-01" and e.range_end == "2026-09-23"


def test_syntax_b_legacy_dates():
    e = epoch.parse_epoch("август=root:2026-08-01:2026-08-31:Август")
    assert e.home == "root"
    assert e.start == "2026-08-01" and e.end == "2026-08-31" and e.period == "Август"
    assert e.runs is None


def test_syntax_v_legacy_study_dir():
    e = epoch.parse_epoch("запись=study/fresh")
    assert e.study_dir == "study/fresh"
    assert e.runs is None and e.start is None


def test_repeated_name_syntax_a_appends_runs():
    parsed = epoch.parse_epochs([
        "история=epochs/e-archive:b5/titrc-u500-trail",
        "история=epochs/e-archive:b5/titrc-v2",
    ])
    assert parsed["история"].runs == ["b5/titrc-u500-trail", "b5/titrc-v2"]


def test_repeated_name_syntax_b_is_an_error():
    with pytest.raises(ValueError):
        epoch.parse_epochs([
            "август=root:2026-08-01:2026-08-31",
            "август=root:2026-08-01:2026-08-15",
        ])


def test_epoch_without_equals_sign_is_an_error():
    with pytest.raises(ValueError):
        epoch.parse_epoch("root:b5/titrc")


def test_parse_epochs_repeat_name_other_home_keeps_both_parts():
    """Повтор имени с другим домом (exit-sim/loss-days/family-titrate) — обе части в `parts`, ничего не теряется."""
    parsed = epoch.parse_epochs([
        "сентябрь=epochs/e-archive:b5/titrc-u500r",
        "сентябрь=.:b5/titrc-u500r",
    ])
    e = parsed["сентябрь"]
    assert e.parts == [("epochs/e-archive", ["b5/titrc-u500r"]), (".", ["b5/titrc-u500r"])]
    assert e.home == "epochs/e-archive" and e.runs == ["b5/titrc-u500r"]
