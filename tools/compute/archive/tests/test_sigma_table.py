#!/usr/bin/env python3
"""Фикстурный тест sigma-table.py (В-131): σ = √Σr² минутных лог-доходностей за 240 мин в bps, окно по
последней закрытой минуте; < 200 доходностей — строки нет; дыра в свечах гасит доходность и себе, и
следующей минуте; два каталога склеиваются, расхождение закрытий — отказ.

Запуск: `python -m pytest tools/compute/tests -q` (на Windows вывод кириллицы — PYTHONIOENCODING=utf-8).
"""
from __future__ import annotations

import csv
import math
import os
import subprocess
import sys
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "sigma-table.py"
MIN = 60_000
T0 = 1_788_000_000_000 // MIN * MIN
ENV = dict(os.environ, PYTHONIOENCODING="utf-8")


def klines(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["minute_ms", "open", "high", "low", "close", "volume"])
        for m, c in rows:
            w.writerow([m, c, c, c, c, 1])


def run(tmp: Path, *dirs: Path):
    out = tmp / "out"
    args = [sys.executable, str(MODULE), "--out-dir", str(out), "--symbols", "AAAUSDT"]
    for d in dirs:
        args += ["--klines", str(d)]
    p = subprocess.run(args, capture_output=True, text=True, env=ENV, encoding="utf-8")
    rows = None
    if p.returncode == 0:
        with open(out / "sigma-AAAUSDT.csv", encoding="utf-8") as f:
            rows = {int(r["window_end_ms"]): float(r["sigma_bps"]) for r in csv.DictReader(f)}
    return p, rows


def test_sigma_window_threshold_and_gap(tmp_path: Path) -> None:
    # 300 минут, цена растёт на 0,1 % в минуту: каждая доходность ln(1.001).
    r = math.log(1.001)
    closes = [(T0 + i * MIN, 100 * 1.001 ** i) for i in range(300)]
    klines(tmp_path / "a" / "ref-AAAUSDT-1m.csv", closes)
    p, rows = run(tmp_path, tmp_path / "a")
    assert p.returncode == 0, p.stderr
    # Первая доходность — у минуты 1; окно конца E — минуты E−240…E−1. 200 доходностей впервые при
    # E = T0 + 201 мин (минуты −39…200 → доходности 1…200).
    assert min(rows) == T0 + 201 * MIN
    assert abs(rows[T0 + 201 * MIN] - math.sqrt(200) * r * 1e4) < 1e-4
    full = T0 + 241 * MIN
    assert abs(rows[full] - math.sqrt(240) * r * 1e4) < 1e-4
    assert max(rows) == T0 + 300 * MIN

    # Дыра на минуте 250: пропадают доходности 250 и 251 → у окон, где они есть, 238 из 240.
    holed = [x for x in closes if x[0] != T0 + 250 * MIN]
    klines(tmp_path / "b" / "ref-AAAUSDT-1m.csv", holed)
    p, rows = run(tmp_path, tmp_path / "b")
    assert p.returncode == 0, p.stderr
    assert abs(rows[T0 + 280 * MIN] - math.sqrt(238) * r * 1e4) < 1e-4


def test_two_dirs_merge_and_conflict_refuses(tmp_path: Path) -> None:
    closes = [(T0 + i * MIN, 100 + i) for i in range(260)]
    klines(tmp_path / "x" / "ref-AAAUSDT-1m.csv", closes[:130])
    klines(tmp_path / "y" / "ref-AAAUSDT-1m.csv", closes[120:])
    klines(tmp_path / "z" / "ref-AAAUSDT-1m.csv", closes)
    p, merged = run(tmp_path, tmp_path / "x", tmp_path / "y")
    assert p.returncode == 0, p.stderr
    p, whole = run(tmp_path, tmp_path / "z")
    assert merged == whole and merged
    klines(tmp_path / "w" / "ref-AAAUSDT-1m.csv", [(T0 + 125 * MIN, 999)])
    p, _ = run(tmp_path, tmp_path / "z", tmp_path / "w")
    assert p.returncode != 0 and "закрытие" in p.stderr
