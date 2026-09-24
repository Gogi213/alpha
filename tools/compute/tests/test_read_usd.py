#!/usr/bin/env python3
"""Сводки титрования (`exit-titration-read.py`, `titration-read.py`) печатают доллары по фактической позиции
сделок (`_lib.fill_usd_by_form`, В-93), а не net × n / 10 — условную позицию $1000 (ревью 24.09).

Запуск: `python -m pytest tools/compute/tests -q`.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


ROUNDS_HEAD = "symbol,day_utc,form,signal_index,t0_ns,dir,entry_px,exit_px,qty,net_bps,reason,exit_ns,fill_frac,entry_vwap,legs_filled,legs_rejected\n"


def test_exit_read_dollars_come_from_filled_size(tmp_path):
    home = tmp_path / "home"
    # Две сделки формы F: $250 × +100 bps = +$2.5 и $125 × −40 bps = −$0.5 → +$2.0 (условная $1000 дала бы +3.0).
    write(home / "b5/titrx-t-fix/2026-09-16/t-bid-btc1h-q1/rounds.csv",
          "# шапка сетки\n" + ROUNDS_HEAD
          + "AUSDT,2026-09-16,F,0,1,1,10,10.1,25,100,take,2,1.0,10,2,1\n"
          + "BUSDT,2026-09-16,F,1,3,1,5,4.98,25,-40,stop,4,1.0,5,1,1\n")
    write(home / "study/placebo-titrx-t-fix-t-bid-btc1h-q1.csv",
          "form,n,net_bps,control_bps,excess_bps,days_excess_pos,days\nF,2,30,0,30,1,1\n")
    m = load("exit_read", "exit-titration-read.py")
    rows = m.read_epoch(str(home / "study"), "titrx-t")
    assert rows[("t-bid-btc1h-q1", "F")]["usd"] == pytest.approx(2.0)


def test_lib_fill_usd_skips_rows_without_size(tmp_path):
    lib = load("_lib", "_lib.py")
    write(tmp_path / "run/2026-09-16/S/rounds.csv",
          "form,net_bps,qty,entry_vwap,entry_px\nF,100,2,50,50\nF,50,,,\n")
    assert lib.fill_usd_by_form(str(tmp_path / "run"), "S") == {"F": pytest.approx(1.0)}


def test_exit_read_run_filter_keeps_runs_apart(tmp_path):
    """Ревью 24.09: у тега titrg лежат прогоны u500r и v1 с теми же (набор, форма) — без --run строки
    перезаписывали друг друга в порядке listdir; с --run берётся только свой прогон."""
    home = tmp_path / "home"
    for run, net in [("u500r", 30), ("v1", -70)]:
        write(home / f"study/placebo-titrg-{run}-t-bid-btc1h-q1.csv",
              f"form,n,net_bps,control_bps,excess_bps,days_excess_pos,days\nF,2,{net},0,{net},1,1\n")
    m = load("exit_read2", "exit-titration-read.py")
    rows = m.read_epoch(str(home / "study"), "titrg", "u500r")
    assert rows[("t-bid-btc1h-q1", "F")]["net"] == 30
    rows_v1 = m.read_epoch(str(home / "study"), "titrg", "v1")
    assert rows_v1[("t-bid-btc1h-q1", "F")]["net"] == -70
