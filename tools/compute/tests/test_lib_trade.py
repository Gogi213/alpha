#!/usr/bin/env python3
"""`_lib.trade` (T-21): синонимы полей, мс/нс, месяц по сигналу (не по `t1`).

Запуск: `python -m pytest tools/compute/tests -q`.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


def load_trade():
    spec = importlib.util.spec_from_file_location("_lib.trade", HERE / "_lib" / "trade.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_lib.trade"] = mod  # dataclass с отложенными аннотациями резолвит cls.__module__
    spec.loader.exec_module(mod)
    return mod


trade = load_trade()


def test_synonyms_symbol_and_entry_and_fill():
    t = trade.from_row({
        "sym": "BTCUSDT", "t0_ns": 1_000_000_000, "t1_ns": 2_000_000_000,
        "net": "80", "qty": "2", "entry_px": "50000", "fill": "0.5",
    })
    assert t.symbol == "BTCUSDT"
    assert t.net_bps == 80.0
    assert t.notional_usd == 100_000.0  # qty × entry_px
    assert t.fill_frac == 0.5


def test_ms_fields_convert_to_ns_by_field_name_not_magnitude():
    t = trade.from_row({
        "symbol": "ETHUSDT", "t0_ms": 1_000, "exit_ms": 2_000,
        "net_bps": 10, "qty": 1, "entry_vwap": 3000,
    })
    assert t.t0_ns == 1_000 * 1_000_000
    assert t.t1_ns == 2_000 * 1_000_000


def test_usd_in_trade_row_means_notional_not_pnl():
    """В-93: `usd` строки сделки — размер позиции, не готовый pnl."""
    t = trade.from_row({
        "symbol": "SOLUSDT", "t0": 1, "t1": 2, "net_bps": 100, "usd": 500,
    })
    assert t.notional_usd == 500
    assert t.pnl_usd == 5.0  # 100/1e4 * 500


def test_pnl_synonym_without_notional_backs_out_notional():
    """Источник даёт net_bps + готовый pnl без qty/entry/usd (агрегат под видом сделки) —
    notional восстанавливается, чтобы свойство `pnl_usd` совпало с тем, что источник имел в виду."""
    t = trade.from_row({"symbol": "X", "t0": 1, "t1": 2, "net_bps": 200, "pnl": 4.0})
    assert t.pnl_usd == 4.0


def test_missing_t0_t1_raises_keyerror():
    import pytest
    with pytest.raises(KeyError):
        trade.from_row({"symbol": "X", "net_bps": 1})


def test_month_of_trade_is_by_signal_not_exit_31_aug_to_01_sep_is_august():
    t0 = dt.datetime(2026, 8, 31, 23, 50, tzinfo=dt.timezone.utc).timestamp() * 1e9
    t1 = dt.datetime(2026, 9, 1, 0, 10, tzinfo=dt.timezone.utc).timestamp() * 1e9
    t = trade.from_row({
        "symbol": "BTCUSDT", "t0_ns": int(t0), "t1_ns": int(t1),
        "net_bps": 10, "qty": 1, "entry_vwap": 100,
    })
    assert t.day_utc == "2026-08-31"
    assert t.month == "2026-08"


def test_funding_reduces_pnl():
    t = trade.from_row({
        "symbol": "X", "t0": 1, "t1": 2, "net_bps": 100, "usd": 1000, "funding_usd": 0.7,
    })
    assert t.pnl_usd == 10.0 - 0.7
