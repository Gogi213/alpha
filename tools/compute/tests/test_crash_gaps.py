#!/usr/bin/env python3
"""Фикстурный тест crash-gaps.py (В-88): защита от пустых выборок — median()/min() по пустому списку
раньше падали (StatisticsError/ValueError), когда ни одна монета не задела стоп или нет свечей на выход.

Запуск: `python -m pytest tools/compute/tests -q` (на Windows вывод кириллицы — PYTHONIOENCODING=utf-8).
"""
from __future__ import annotations

import csv
import os
import subprocess
import sys
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "crash-gaps.py"
MIN = 60_000
ENV = dict(os.environ, PYTHONIOENCODING="utf-8")


def write_csv(path: Path, header, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def run(args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(MODULE), *args], capture_output=True, encoding="utf-8", env=ENV)


def test_no_klines_no_crash_and_says_so(tmp_path):
    """Каталог свечей пуст: ни одна монета не может задеть стоп, и на выход выключателя тоже нечем
    закрыть — раньше это падало на median(lo)/lo[0] и median(out)/min(out) по пустым спискам."""
    kdir = tmp_path / "klines"
    kdir.mkdir()
    btc = tmp_path / "btc.csv"
    # BTC валится за 1 ч на минуте 60 — выключатель должен сработать (trig найден), но монет нет
    write_csv(btc, ["minute_ms", "low", "close"],
              [[i * MIN, 1.0, 1.0] for i in range(60)] + [[60 * MIN, 0.5, 0.5]])
    res = run(["--klines", str(kdir), "--btc", str(btc), "--entry-from", "1970-01-01T00:00",
               "--entry-to", "1970-01-01T00:05", "--hold-from", "1970-01-01T00:00",
               "--stop-pct", "2", "--hold-min", "120", "--kill-bps", "100"])
    assert res.returncode == 0, res.stderr
    assert "стоп не задет ни у одной монеты" in res.stdout
    assert "нет свечей ни у одной монеты на выход" in res.stdout


def test_stop_not_hit_no_crash(tmp_path):
    """Монета есть, но цена не двигается — стоп не задет; median/min по пустой выборке не должны падать."""
    kdir = tmp_path / "klines"
    write_csv(kdir / "ref-AAAUSDT-1m.csv", ["minute_ms", "low", "close"],
              [[i * MIN, 1.0, 1.0] for i in range(30)])
    btc = tmp_path / "btc.csv"
    write_csv(btc, ["minute_ms", "low", "close"], [[i * MIN, 1.0, 1.0] for i in range(30)])
    res = run(["--klines", str(kdir), "--btc", str(btc), "--entry-from", "1970-01-01T00:00",
               "--entry-to", "1970-01-01T00:05", "--hold-from", "1970-01-01T00:00",
               "--stop-pct", "50", "--hold-min", "20", "--kill-bps", "100"])
    assert res.returncode == 0, res.stderr
    assert "стоп не задет ни у одной монеты" in res.stdout
    assert "не сработал" in res.stdout  # плоский BTC — выключатель не сработал


def test_stop_hit_and_kill_switch_still_report_normally(tmp_path):
    """Регресс: обычный (непустой) случай считает медианы/худшие как раньше."""
    kdir = tmp_path / "klines"
    # монета проваливается на 3-й минуте на -5 % (стоп -2 % задет); данные тянутся дальше выхода
    # выключателя (60-я минута BTC + 1), иначе «выхода» не с чем сравнить (пустая выборка out/bottom)
    rows = [[i * MIN, 1.0, 1.0] for i in range(3)] + [[3 * MIN, 0.95, 0.95]] + \
           [[i * MIN, 0.95, 0.95] for i in range(4, 65)]
    write_csv(kdir / "ref-AAAUSDT-1m.csv", ["minute_ms", "low", "close"], rows)
    btc = tmp_path / "btc.csv"
    # BTC валится за 1 ч на минуте 60
    write_csv(btc, ["minute_ms", "low", "close"],
              [[i * MIN, 1.0, 1.0] for i in range(60)] + [[60 * MIN, 0.5, 0.5]] +
              [[61 * MIN, 0.5, 0.5]])
    res = run(["--klines", str(kdir), "--btc", str(btc), "--entry-from", "1970-01-01T00:00",
               "--entry-to", "1970-01-01T00:02", "--hold-from", "1970-01-01T00:00",
               "--stop-pct", "2", "--hold-min", "120", "--kill-bps", "100"])
    assert res.returncode == 0, res.stderr
    assert "худший исход по монете" in res.stdout
    assert "закрыто: медиана" in res.stdout  # непустой out — не «нет свечей на выход»


if __name__ == "__main__":
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-q"]))
