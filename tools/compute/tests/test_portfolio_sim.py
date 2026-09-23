#!/usr/bin/env python3
"""Фикстурный тест portfolio-sim.py (P1, В-88): минутная переоценка открытых позиций (mark-to-market)
против модели «по закрытиям», одна позиция на монету, направление в формуле выключателя BTC, дни только
своих суток в load_btc1h, пустые выборки без падений.

Запуск: `python -m pytest tools/compute/tests -q` (на Windows вывод кириллицы — PYTHONIOENCODING=utf-8).
"""
from __future__ import annotations

import csv
import datetime as dt
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / "portfolio-sim.py"
MIN_MS = 60_000


def load():
    spec = importlib.util.spec_from_file_location("portfolio_sim", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_csv(path: Path, header, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def mkrow(sym, t0_min, t1_min, net_bps=100.0, dir_=1, entry=1.0, fee=0.0, usd=1000.0, fill=1.0, reason="take"):
    """Строка формы load_run() — минуты переводятся в ns, чтобы не тащить в тест бэктест-CSV целиком."""
    return {"t0": t0_min * MIN_MS * 1_000_000, "t1": t1_min * MIN_MS * 1_000_000, "sym": sym, "net": net_bps,
            "reason": reason, "entry": entry, "fee": fee, "dir": dir_, "usd": usd, "fill": fill}


# ---------- load_btc1h: только минуты своих суток (как titration-points.py), без склейки через set() ----------

def test_load_btc1h_own_day_window(tmp_path):
    m = load()
    reg = tmp_path / "study" / "regime"
    d01 = int(dt.datetime(2025, 10, 10, tzinfo=dt.timezone.utc).timestamp() * 1000)
    d02 = d01 + 86_400_000
    header = ["minute_ms", "btc_ret_1h_bps"]
    # файл суток 10-го несёт и свои минуты, и (как реально пишет regime.py) минуты соседних суток —
    # они не должны попасть в результат из ЭТОГО файла
    write_csv(reg / "2025-10-10.csv", header,
              [[d01 + i * MIN_MS, i] for i in range(3)] + [[d02 + i * MIN_MS, 9999] for i in range(2)])
    write_csv(reg / "2025-10-11.csv", header, [[d02 + i * MIN_MS, 100 + i] for i in range(3)])
    minutes, vals = m.load_btc1h(str(tmp_path))
    assert minutes == [d01, d01 + MIN_MS, d01 + 2 * MIN_MS, d02, d02 + MIN_MS, d02 + 2 * MIN_MS]
    assert vals == [0, 1, 2, 100, 101, 102]  # значения суток 10-го из файла 11-го (9999) не вошли


def test_load_btc1h_empty_dir_no_crash(tmp_path):
    m = load()
    assert m.load_btc1h(str(tmp_path)) == ([], [])


# ---------- Klines: последняя известная свеча, покрытие монеты ----------

def test_klines_last_close_fallback_and_coverage(tmp_path):
    m = load()
    kdir = tmp_path / "klines"
    write_csv(kdir / "ref-AAAUSDT-1m.csv", ["minute_ms", "close"],
              [[0, 1.0], [MIN_MS, 1.1], [3 * MIN_MS, 1.3]])  # дыра на 2*MIN_MS
    k = m.Klines([str(kdir)])
    assert k.covered("AAAUSDT") is True
    assert k.covered("NOPEUSDT") is False
    assert k.close("AAAUSDT", MIN_MS) == pytest.approx(1.1)
    assert k.close("AAAUSDT", 2 * MIN_MS) is None  # дыра — close() не подменяет
    assert k.last_close("AAAUSDT", 2 * MIN_MS) == pytest.approx(1.1)  # последняя известная
    assert k.last_close("AAAUSDT", 3 * MIN_MS) == pytest.approx(1.3)
    assert k.last_close("AAAUSDT", -MIN_MS) is None  # раньше первой свечи — не знаем
    assert k.last_close("NOPEUSDT", MIN_MS) is None


# ---------- minute_curve против closed_drawdown: просадка внутри хода видна только в минутной ----------

def test_minute_curve_sees_intratrade_dip_closed_does_not(tmp_path):
    m = load()
    kdir = tmp_path / "klines"
    # лонг: вход 1.00, ход вниз до 0.90 на 5-й минуте (−10 %), назад и к выходу (10-я минута) — уже +5 %
    closes = [1.00, 0.98, 0.96, 0.94, 0.92, 0.90, 0.95, 1.00, 1.02]
    write_csv(kdir / "ref-AAAUSDT-1m.csv", ["minute_ms", "close"],
              [[i * MIN_MS, c] for i, c in enumerate(closes)])
    k = m.Klines([str(kdir)])
    trade = {"t0": 0, "t1": 10 * MIN_MS * 1_000_000, "sym": "AAAUSDT", "pnl": 50.0, "usd": 1000.0,
              "fill": 1.0, "reason": "take", "dir": 1, "entry": 1.00, "fee": 0.0}

    dd_usd, dd_pct = m.closed_drawdown([trade], 1000.0)
    assert (dd_usd, dd_pct) == (0.0, 0.0)  # один шаг на закрытии, сразу в плюс — просадки не видно

    mm = m.minute_curve([trade], k, 1000.0, 50.0)
    assert mm["dd_usd"] == pytest.approx(100.0)  # -10 % от $1000 на 5-й минуте
    assert mm["dd_pct"] == pytest.approx(10.0)
    assert mm["rf"] == pytest.approx(0.5)  # прибыль 50 / просадка 100
    assert mm["rec_days"] == pytest.approx(7 * 60 / 86_400, rel=1e-6)  # пик 1-й мин. → снова там же 8-й
    assert mm["unrecovered"] is False
    assert mm["n_no_klines"] == 0


def test_minute_curve_no_klines_falls_back_to_closed(tmp_path):
    m = load()
    k = m.Klines([str(tmp_path / "nope")])  # каталога вовсе нет — как отсутствие свечей у монеты
    trade = {"t0": 0, "t1": 10 * MIN_MS * 1_000_000, "sym": "NOPEUSDT", "pnl": 50.0, "usd": 1000.0,
              "fill": 1.0, "reason": "take", "dir": 1, "entry": 1.00, "fee": 0.0}
    mm = m.minute_curve([trade], k, 1000.0, 50.0)
    assert mm["n_no_klines"] == 1
    assert mm["dd_usd"] == pytest.approx(0.0)  # без свечей — переоценки нет, только закрытие
    assert mm["rf"] is None


def test_minute_curve_and_closed_drawdown_empty_no_crash(tmp_path):
    m = load()
    k = m.Klines([str(tmp_path)])
    assert m.minute_curve([], k, 1000.0, 0.0) == {
        "dd_usd": 0.0, "dd_pct": 0.0, "rf": None, "rec_days": 0.0, "unrecovered": False, "n_no_klines": 0}
    assert m.closed_drawdown([], 1000.0) == (0.0, 0.0)


# ---------- simulate(): одна позиция на монету ----------

def test_simulate_skips_second_entry_while_symbol_occupied(tmp_path):
    m = load()
    k = m.Klines([str(tmp_path)])
    rows = [mkrow("AAAUSDT", 0, 100), mkrow("AAAUSDT", 50, 150)]  # второй входит, пока первый ещё открыт
    r = m.simulate(rows, ([], []), k, 1000.0, 0, 0.0, 0.0, set(), 59.7)
    assert r["n"] == 1
    assert r["skip"]["занята"] == 1


def test_simulate_allows_reentry_after_close(tmp_path):
    m = load()
    k = m.Klines([str(tmp_path)])
    rows = [mkrow("AAAUSDT", 0, 50), mkrow("AAAUSDT", 60, 150)]  # второй входит после закрытия первого
    r = m.simulate(rows, ([], []), k, 1000.0, 0, 0.0, 0.0, set(), 59.7)
    assert r["n"] == 2
    assert r["skip"]["занята"] == 0


# ---------- выключатель BTC: направление сделки ----------

def test_kill_switch_respects_short_direction(tmp_path):
    m = load()
    kdir = tmp_path / "klines"
    write_csv(kdir / "ref-AAAUSDT-1m.csv", ["minute_ms", "close"], [[11 * MIN_MS, 1.10]])
    k = m.Klines([str(kdir)])
    minutes, vals = [0, 10 * MIN_MS], [0.0, -999.0]  # BTC обваливается на 10-й минуте
    row = {"t0": 0, "t1": 100 * MIN_MS * 1_000_000, "sym": "AAAUSDT", "net": 100.0, "reason": "take",
           "entry": 1.00, "fee": 0.0, "dir": -1, "usd": 1000.0, "fill": 1.0}  # шорт
    r = m.simulate([row], (minutes, vals), k, 1000.0, 0, 0.0, 100.0, set(), 59.7)
    assert r["killed"] == 1
    assert r["no_kline"] == 0
    # цена выросла на 10 % — шорту это убыток; старая формула (без dir) посчитала бы прибыль
    assert r["total_usd"] == pytest.approx(-100.0)


def test_simulate_empty_rows_no_crash(tmp_path):
    m = load()
    k = m.Klines([str(tmp_path)])
    r = m.simulate([], ([], []), k, 1000.0, 0, 0.0, 0.0, set(), 59.7)
    assert r["n"] == 0
    assert r["rf"] is None
    assert r["worst_day"] == "—"


# ---------- CLI целиком: связка занята/dd_closed/n_no_klines в JSON и таблице ----------

def test_cli_end_to_end(tmp_path):
    start = int(dt.datetime(2026, 9, 16, tzinfo=dt.timezone.utc).timestamp() * 1000)
    root = tmp_path / "epoch"
    header = ["symbol", "form", "t0_ns", "exit_ns", "dir", "entry_px", "entry_vwap", "exit_px",
              "qty", "net_bps", "reason", "fill_frac"]
    rows = [
        ["AAAUSDT", "FORM1", start * 1_000_000, (start + 50 * MIN_MS) * 1_000_000,
         1, 1.0, 1.0, 1.05, 1000, 480, "take", 1.0],
        # второй вход — пока первый ещё открыт (закроется на +50 мин, этот входит на +30 мин)
        ["AAAUSDT", "FORM1", (start + 30 * MIN_MS) * 1_000_000, (start + 150 * MIN_MS) * 1_000_000,
         1, 1.0, 1.0, 1.02, 1000, 180, "take", 1.0],
    ]
    write_csv(root / "run1" / "2026-09-16" / "setA" / "rounds.csv", header, rows)
    kdir = root / "klines"
    write_csv(kdir / "ref-AAAUSDT-1m.csv", ["minute_ms", "close"],
              [[start + i * MIN_MS, 1.0] for i in range(200)])
    out_json = tmp_path / "out.json"
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    # ходовые пути в CLI-примере скрипта относительные («.:прогон») — раздел «дом:прогоны» иначе
    # ловит двоеточие диска Windows (C:\…); запускаем с cwd=root и используем относительные пути
    res = subprocess.run(
        [sys.executable, str(MODULE), "--epoch", "эп=.:run1", "--variant", "вар=setA/FORM1",
         "--klines", "klines", "--deposit-usd", "1000", "--position-usd", "1000", "--json", str(out_json)],
        capture_output=True, encoding="utf-8", env=env, cwd=str(root))
    assert res.returncode == 0, res.stderr
    assert "занята" in res.stdout and "закр.дд%" in res.stdout
    grid = json.loads(out_json.read_text(encoding="utf-8"))["grid"]
    assert len(grid) == 1
    g = grid[0]
    assert g["n"] == 1
    assert g["skip"]["занята"] == 1
    assert g["n_no_klines"] == 0
    assert "dd_closed_pct" in g and "dd_closed_usd" in g


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
