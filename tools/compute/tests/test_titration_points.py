#!/usr/bin/env python3
"""Фикстурный тест `titration-points.py`: квинтили по минутам режима, корзины без дыр и наложений,
пустые значения пула не входят в края, края только по суткам окна «истории».

Запуск: `python -m pytest tools/compute/tests -q` (pytest есть локально; на счётной его нет).
"""
from __future__ import annotations

import csv
import importlib.util
import subprocess
import sys
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "titration-points.py"
D01 = 1_788_220_800_000  # 2026-09-01T00:00:00Z
D02 = D01 + 86_400_000
HEADER = ["minute_ms", "pool_ret_1h_bps", "pool_ret_4h_bps", "n_coins",
          "btc_ret_1h_bps", "btc_ret_4h_bps", "eth_ret_1h_bps", "eth_ret_4h_bps"]


def load():
    spec = importlib.util.spec_from_file_location("titration_points", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_day(dir_: Path, day: str, rows):
    with open(dir_ / f"{day}.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(rows)


def test_edges_are_quintiles():
    m = load()
    assert m.quantile_edges(list(range(100))) == [20, 40, 60, 80]


def test_bins_cover_axis_once():
    m = load()
    sets = m.bin_sets("bid", "btc4h", [-50.0, -10.0, 10.0, 50.0])
    names = [n for n, _ in sets]
    specs = [s for _, s in sets]
    assert names == [f"t-bid-btc4h-q{k}" for k in range(1, 6)]
    assert specs[0] == "age=2700,side=bid,btc4h_max=-50.0"
    assert specs[2] == "age=2700,side=bid,btc4h_min=-10.0,btc4h_max=10.0"
    assert specs[4] == "age=2700,side=bid,btc4h_min=50.0"


def test_sets_both_sides_and_age():
    m = load()
    sets = m.build_sets({"btc4h": [1.0, 2.0, 3.0, 4.0]})
    names = [n for n, _ in sets]
    assert len(names) == 2 * (5 + 6)
    assert "t-ask-btc4h-q1" in names and "t-bid-age-120" in names
    assert dict(sets)["t-ask-age-15"] == "age=900,side=ask"


def test_cli_window_and_missing_pool(tmp_path):
    reg = tmp_path / "regime"
    reg.mkdir()
    # Окно — 01–02; сутки 16 вне окна и не должны сдвигать края. Файл 02 несёт и минуты 01 (48 ч) —
    # они не должны войти второй раз.
    m = 60_000
    write_day(reg, "2026-09-01", [[D01 + i * m, "", "", 0, i, i, 0, 0] for i in range(50)])
    write_day(reg, "2026-09-02", [[D01 + i * m, "", "", 0, 1e6, 1e6, 0, 0] for i in range(50)]
              + [[D02 + i * m, i, i, 5, i + 50, i + 50, 0, 0] for i in range(50)])
    write_day(reg, "2026-09-16", [[D02 + i * m, 1e6, 1e6, 5, 1e6, 1e6, 0, 0] for i in range(50)])
    out = tmp_path / "points.csv"
    res = subprocess.run([sys.executable, str(MODULE), "--regime", str(reg), "--from", "2026-09-01",
                          "--to", "2026-09-02", "--out", str(out)], capture_output=True, text=True, check=True)
    rows = {r["axis"]: r for r in csv.DictReader(open(out, encoding="utf-8"))}
    assert rows["btc4h"]["minutes"] == "100" and rows["btc4h"]["q20"] == "20.00"
    assert rows["pool4h"]["minutes"] == "50"  # пустой пул первых суток в края не вошёл
    assert rows["pool4h"]["q80"] == "40.00"
    assert len(res.stdout.split()) == 2 * (4 * 5 + 6)
