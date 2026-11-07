#!/usr/bin/env python3
"""TK-493, П-38 r2 §7.1 K9: чувствительность пола оборота Q3 (x0 / x1 / x10 от назначенных 1 000 единиц котировочной валюты) - обёртка над conv_run.py.

Подменяет `conv_lib.TURN_FLOOR` в процессе (рабочие процессы наследуют значение при fork) и вызывает conv_run.main с остальными аргументами. Кэш рядов и числа класса (classpar_*.json,
К4) не пересчитываются: это чувствительность выбранной комбинации при тех же замороженных числах класса, не новая калибровка.
  python conv_sens.py --floor 0 -- grid --out ... --tag sens_f0 --combos F.json ...
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import conv_lib as L        # noqa: E402
import conv_run as R        # noqa: E402


def main():
    argv = sys.argv[1:]
    if len(argv) < 3 or argv[0] != "--floor" or argv[2] != "--":
        raise SystemExit("usage: conv_sens.py --floor X -- <conv_run args>")
    L.TURN_FLOOR = float(argv[1])
    return R.main(argv[3:])


if __name__ == "__main__":
    sys.exit(main())
