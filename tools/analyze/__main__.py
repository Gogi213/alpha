#!/usr/bin/env python3
"""Слой разбора (В-219, КТ-7): одна точка входа к разбору ГОТОВЫХ сделок — стратегию не считает, бэктесты не запускает.
Запуск из корня репозитория: python tools/analyze <подкоманда> [аргументы]; список — python tools/analyze --help.
Новый разрез = подкоманда здесь, а не `tk*-` скрипт."""
import os, runpy, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SUBS = {   # подкоманда -> (скрипт, смысл)
    "monthly":   ("monthly-pnl.py",       "помесячная прибыль по всем клеткам П-12"),
    "sharpe":    ("p12-sharpe.py",        "ворота В-208: ΔSR к базе, free|B2"),
    "tiers":     ("p12-tiers.py",         "ярусы по SR"),
    "cuts":      ("tk113-coins.py",       "разрез $ по монетам и месяцам"),
    "cells":     ("tk113-cells.py",       "клетки R2 по пулу: $, Шарп, месяцы"),
    "ext":       ("tk113-ext.py",         "ext-клетки в едином окне"),
    "coin-c2":   ("p12-coin-c2.py",       "монета с наибольшим вкладом (С2)"),
    "tier1":     ("tk114-tier1-select.py", "П-13 ярус 1: отбор"),
    "tier2":     ("tk114-tier2-select.py", "П-13 ярус 2: отбор"),
    "testB":     ("tk114-testB.py",       "П-13 тест Б: победитель против базы"),
    "report":    ("tk114-report2.py",     "П-13 итог: $ по месяцам и монетам, Шарп"),
    "portfolio": ("../compute/portfolio-sim.py", "счёт депозита по сделкам (защиты, просадка)"),
}


def main(argv):
    if len(argv) < 2 or argv[1] in ("-h", "--help") or argv[1] not in SUBS:
        print("подкоманды:\n" + "\n".join(f"  {k:10} {v[1]}" for k, v in SUBS.items()))
        return 0 if len(argv) > 1 and argv[1] in ("-h", "--help") else 2
    path = os.path.normpath(os.path.join(HERE, SUBS[argv[1]][0]))
    sys.argv = [path] + argv[2:]
    sys.path.insert(0, os.path.dirname(path))
    runpy.run_path(path, run_name="__main__")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
