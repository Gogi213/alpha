#!/usr/bin/env python3
"""Фикстурный тест `loss-atoms.py`: три сделки (тейк, стоп, дедлайн) и все атомы A1–A4, A8.

Запуск: `python -m pytest tools/compute/tests -q` (pytest есть локально; на счётной его нет).
Фикстура — крошечные CSV той же формы, что кэши проекта; числа подобраны вручную.
"""
from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / "loss-atoms.py"
FORM = "ladder3x2..20w2-pct2-1to1-7200-ttl1800"
DAY = "2026-09-16"
START_MS = 1_789_516_800_000  # 2026-09-16T00:00:00Z
TICK = 0.01
PXS = {0: 1.00, 10: 1.03, 11: 1.00, 120: 0.97, 121: 1.00}


def _spec():
    spec = importlib.util.spec_from_file_location("loss_atoms", MODULE)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def write(path: Path, header: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        w.writerows(rows)


@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    root = tmp_path
    (root / "root").mkdir(parents=True)
    write(root / "root" / "instruments.csv", ["symbol", "tick_size"],
          [{"symbol": "TESTUSDT", "tick_size": "0.01"}])

    rounds = [
        # тейк: вход 1.00, выход 1.02, +180 bps, 30 мин
        dict(symbol="TESTUSDT", day_utc=DAY, form=FORM, signal_index="0",
             t0_ns=str((START_MS + 0) * 1_000_000), dir="1", entry_px="1.0000", exit_px="1.0200",
             qty="10", net_bps="180", reason="take",
             exit_ns=str((START_MS + 30 * 60_000) * 1_000_000), fill_frac="1.0",
             entry_vwap="1.0000", legs_filled="1", legs_rejected="0"),
        # стоп: −200 bps, 5 мин
        dict(symbol="TESTUSDT", day_utc=DAY, form=FORM, signal_index="1",
             t0_ns=str((START_MS + 120 * 60_000) * 1_000_000), dir="1", entry_px="1.0000",
             exit_px="0.9800", qty="10", net_bps="-200", reason="stop",
             exit_ns=str((START_MS + 125 * 60_000) * 1_000_000), fill_frac="0.5",
             entry_vwap="1.0000", legs_filled="1", legs_rejected="0"),
        # дедлайн: −5 bps, 120 мин, цена ровно на входе
        dict(symbol="TESTUSDT", day_utc=DAY, form=FORM, signal_index="2",
             t0_ns=str((START_MS + 200 * 60_000) * 1_000_000), dir="1", entry_px="1.0000",
             exit_px="0.9995", qty="10", net_bps="-5", reason="deadline",
             exit_ns=str((START_MS + 320 * 60_000) * 1_000_000), fill_frac="1.0",
             entry_vwap="1.0000", legs_filled="1", legs_rejected="0"),
    ]
    path = root / "grid" / "rounds.csv"
    path.parent.mkdir(parents=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write("# lob bounce-grid: fixture\n")
        w = csv.DictWriter(f, fieldnames=list(rounds[0]))
        w.writeheader()
        w.writerows(rounds)

    # подходы: три стены на трёх взводах; у первой — соседняя цена (проверка выбора стены)
    ap = []
    for i, t0_min in enumerate([0, 120, 200]):
        for pt in ([101, 100] if i == 0 else [100]):
            ap.append(dict(day_utc=DAY, side="bid", price_tick=str(pt), approach_index=str(i),
                           arm_ms=str(START_MS + t0_min * 60_000), age_ms="600000",
                           arm_dist_bps="10", birth_ms=str(START_MS + t0_min * 60_000 - 600_000),
                           size_at_arm="5000", best_own_tick="101", best_opp_tick="99",
                           flow_1h_lots="3000", strength_w10_pct="100", strength_w20_pct="120",
                           strength_w50_pct="80", touch_start_ms="", disarm_ms="",
                           duration_ms="0", disarm_reason="level_death"))
    write(root / "approaches" / "D20" / DAY / "approaches-TESTUSDT.csv", list(ap[0]), ap)

    # касания: предшественник (для repeat_before и прокси потока) + смерть после входа
    touches = [
        dict(day_utc=DAY, side="bid", price_tick="100", touch_index="0",
             start_ms=str(START_MS - 600_000), end_ms=str(START_MS - 300_000), duration_ms="300000",
             birth_ms=str(START_MS - 1_200_000), age_ms="600000", size_at_touch="40000",
             size_max_before="50000", traded_during="25000", frontrun_lots="900",
             frontrun_tick="99", swept_lots="0", round_zeros="0", ended_by_death="false",
             stack_levels="2", dist_bps="5"),
        # после взвода №1: съели (traded/size_max = 0.6)
        dict(day_utc=DAY, side="bid", price_tick="100", touch_index="1",
             start_ms=str(START_MS + 20 * 60_000), end_ms=str(START_MS + 21 * 60_000),
             duration_ms="60000", birth_ms=str(START_MS - 1_200_000), age_ms="2100000",
             size_at_touch="30000", size_max_before="50000", traded_during="30000",
             frontrun_lots="0", frontrun_tick="", swept_lots="0", round_zeros="0",
             ended_by_death="true", stack_levels="2", dist_bps="5"),
        # после взвода №2: пробили насквозь
        dict(day_utc=DAY, side="bid", price_tick="100", touch_index="2",
             start_ms=str(START_MS + 122 * 60_000), end_ms=str(START_MS + 123 * 60_000),
             duration_ms="60000", birth_ms=str(START_MS - 1_200_000), age_ms="7500000",
             size_at_touch="10000", size_max_before="50000", traded_during="9000",
             frontrun_lots="0", frontrun_tick="", swept_lots="10000", round_zeros="0",
             ended_by_death="false", stack_levels="2", dist_bps="5"),
    ]
    write(root / "touches" / DAY / "touches-TESTUSDT.csv", list(touches[0]), touches)

    # середина по минутам: 1.00, на 10-й минуте 1.03, на 120-й 0.97
    mids = []
    for m in range(0, 601):
        px = PXS.get(m, 1.00)
        mids.append({"minute_ms": str(START_MS + m * 60_000), "mid2x": str(int(round(px / TICK * 2)))})
    write(root / "touches" / DAY / "mids1m-TESTUSDT.csv", ["minute_ms", "mid2x"], mids)

    reg = []
    for m in range(0, 601):
        reg.append({"minute_ms": str(START_MS + m * 60_000), "pool_ret_1h_bps": "-12.5",
                    "pool_ret_4h_bps": "-30.0", "n_coins": "90", "btc_ret_1h_bps": "-20.0",
                    "btc_ret_4h_bps": "-55.5", "eth_ret_1h_bps": "-10.0", "eth_ret_4h_bps": "-40.0"})
    write(root / "regime" / f"{DAY}.csv", list(reg[0]), reg)
    return root


def run(tree: Path, out: Path, extra: list[str] | None = None) -> list[dict]:
    mod = _spec()
    argv = ["--grid-dir", str(tree / "grid"), "--form", FORM,
            "--touches", str(tree / "touches"), "--approaches", str(tree / "approaches" / "D20"),
            "--regime", str(tree / "regime"), "--root", str(tree / "root"), "--out", str(out)]
    assert mod.main(argv + (extra or [])) == 0
    with out.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_atoms_of_three_trades(tree: Path, tmp_path: Path) -> None:
    rows = run(tree, tmp_path / "out.csv")
    assert [r["reason"] for r in rows] == ["take", "stop", "deadline"]
    by = {r["reason"]: r for r in rows}

    # деньги — полный лот (В-83), доля справочно
    assert float(by["take"]["pnl_usd"]) == pytest.approx(18.0)
    assert float(by["stop"]["pnl_usd"]) == pytest.approx(-20.0)
    assert float(by["stop"]["fill_frac"]) == pytest.approx(0.5)
    assert float(by["deadline"]["pnl_usd"]) == pytest.approx(-0.5)

    # A1 рынок по минуте взвода
    assert float(by["take"]["btc_4h"]) == pytest.approx(-55.5)
    assert float(by["stop"]["pool_1h"]) == pytest.approx(-12.5)
    assert float(by["deadline"]["eth_4h"]) == pytest.approx(-40.0)

    # A2 стена: возраст, размер в долларах, повтор, сила, поток; вход = цена стены
    t = by["take"]
    assert float(t["wall_age_min"]) == pytest.approx(10.0)
    assert float(t["wall_size_lots"]) == pytest.approx(5000)
    assert float(t["wall_size_usd"]) == pytest.approx(5000 * 100 * TICK)
    assert float(t["entry_vs_wall_bps"]) == pytest.approx(0.0)
    assert float(t["repeat_before"]) == pytest.approx(1)
    assert float(t["strength_w20"]) == pytest.approx(120)
    assert float(t["flow_1h_lots"]) == pytest.approx(3000)
    assert float(t["flow_proxy_traded_during"]) == pytest.approx(25000)
    assert float(t["flow_proxy_frontrun_lots"]) == pytest.approx(900)
    assert t["sell_15m_lots"] == "" and t["arm_to_entry_min"] == ""

    # A3 судьба стены
    assert t["wall_fate"] == "съели"
    assert float(t["wall_fate_min"]) == pytest.approx(20.0)
    assert float(t["size_at_exit_ratio"]) == pytest.approx(0.6)
    assert by["stop"]["wall_fate"] == "пробили"

    # A4 ход цены: тейк был на пути у первой сделки, провал — у второй
    assert float(t["favour_15m"]) == pytest.approx(300.0)
    assert float(t["favour_hold"]) == pytest.approx(300.0)
    assert float(t["adverse_hold"]) == pytest.approx(0.0)
    assert float(t["reached_take"]) == 1
    assert float(t["mid_at_fate_bps"]) == pytest.approx(0.0)
    assert float(by["stop"]["adverse_5m"]) == pytest.approx(300.0)
    assert float(by["stop"]["adverse_hold"]) == pytest.approx(300.0)
    assert float(by["stop"]["reached_take"]) == 0
    assert float(by["deadline"]["mid_at_deadline_bps"]) == pytest.approx(0.0)
    assert by["deadline"]["mid_at_fate_bps"] == ""  # касания после взвода не было
    assert float(by["take"]["hold_min"]) == pytest.approx(30.0)

    # A8 время
    assert [int(r["hour_utc"]) for r in rows] == [0, 2, 3]


def test_dry_run_writes_nothing(tree: Path, tmp_path: Path) -> None:
    out = tmp_path / "dry.csv"
    mod = _spec()
    assert mod.main(["--grid-dir", str(tree / "grid"), "--form", FORM,
                     "--touches", str(tree / "touches"),
                     "--approaches", str(tree / "approaches" / "D20"),
                     "--regime", str(tree / "regime"), "--root", str(tree / "root"),
                     "--out", str(out), "--dry-run"]) == 0
    assert not out.exists()


def test_missing_cache_is_empty_not_crash(tree: Path, tmp_path: Path) -> None:
    """Нет файла подхода — сделка остаётся, атомы пустые, счётчик в stderr."""
    (tree / "approaches" / "D20" / DAY / "approaches-TESTUSDT.csv").unlink()
    rows = run(tree, tmp_path / "out.csv")
    assert len(rows) == 3
    assert rows[0]["wall_fate"] == "нет_стены"
    assert rows[0]["wall_age_min"] == ""
    assert rows[0]["btc_1h"] != ""  # рынок считается независимо


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
