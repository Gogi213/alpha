#!/usr/bin/env python3
"""Точки титрования живой формы F10 по режиму рынка и возрасту стены (план 2026-09-23, G4/G5).

Точки — из данных, не назначенные. Режим — ход к началу минуты (bps) по осям `bounce-grid --set`:
btc4h, btc1h, pool4h, pool1h (`study/regime/<сутки>.csv`, regime.py). Края — квинтили оси по **всем
минутам** суток эпохи «история» (режим рынка, а не выборка наших сделок): корзина q1 — пятая часть
времени с самым сильным падением, q5 — с самым сильным ростом. Края считаются только по «истории» и
к записи применяются как есть.

Каждая корзина — набор `bounce-grid --set` поверх базы стороны (`age=2700,side=bid|ask`):
`t-<сторона>-<ось>-q<k>`. Возраст стены — накопительно, точки прежних ночных наборов
(15/30/45/60/90/120 мин): `t-<сторона>-age-<мин>`.

Файл режима суток несёт и предыдущие сутки (48 ч, окно 4 ч захватывает прошлые сутки) — в края
идут только минуты своих суток файла, каждая минута один раз (метка v1 23.09 считалась со сдвоенными
минутами BTC; её наборы заморожены в `study/titration-sets-v1.txt`).
Пул (`pool*`) в первые 1 ч / 4 ч суток пуст (regime.py считает его по минутным серединам суток) —
такие минуты в края не входят, а касание без значения оси в корзину не попадает (правило `--set`).

    titration-points.py --regime <study/regime> --from 2026-09-01 --to 2026-09-15 --out <csv>
Выход: строка наборов для SETS (stdout), таблица краёв (--out), сводка краёв (stderr).
"""
import argparse
import csv
import importlib.util
import os
import sys

_lib_spec = importlib.util.spec_from_file_location(
    "_lib", os.path.join(os.path.dirname(os.path.abspath(__file__)), "_lib.py"))
_lib = importlib.util.module_from_spec(_lib_spec)
assert _lib_spec and _lib_spec.loader
_lib_spec.loader.exec_module(_lib)

AXES = {
    "btc4h": "btc_ret_4h_bps",
    "btc1h": "btc_ret_1h_bps",
    "pool4h": "pool_ret_4h_bps",
    "pool1h": "pool_ret_1h_bps",
}
SIDES = ["bid", "ask"]
AGE_MIN = [15, 30, 45, 60, 90, 120]
BASE_AGE_SECS = 2700
BINS = 5


def quantile_edges(values, bins=BINS):
    """Внутренние края `bins` равных по числу корзин (bins − 1 чисел) по выборке."""
    v = sorted(values)
    if len(v) < bins:
        raise ValueError(f"мало значений для {bins} корзин: {len(v)}")
    return [v[(len(v) * k) // bins] for k in range(1, bins)]


def bin_sets(side, axis, edges):
    """Наборы-корзины оси: первая — только `_max`, последняя — только `_min`."""
    out = []
    bounds = [None] + [round(e, 2) for e in edges] + [None]
    for k in range(len(bounds) - 1):
        lo, hi = bounds[k], bounds[k + 1]
        spec = f"age={BASE_AGE_SECS},side={side}"
        if lo is not None:
            spec += f",{axis}_min={lo}"
        if hi is not None:
            spec += f",{axis}_max={hi}"
        out.append((f"t-{side}-{axis}-q{k + 1}", spec))
    return out


def age_sets(side):
    return [(f"t-{side}-age-{m}", f"age={m * 60},side={side}") for m in AGE_MIN]


def build_sets(edges_by_axis):
    sets = []
    for side in SIDES:
        for axis, edges in edges_by_axis.items():
            sets += bin_sets(side, axis, edges)
        sets += age_sets(side)
    return sets


def axis_values(regime_dir, day_from, day_to):
    vals = {axis: [] for axis in AXES}
    days = sorted(n[:-4] for n in os.listdir(regime_dir)
                  if n.endswith(".csv") and n[:4].isdigit() and day_from <= n[:-4] <= day_to)
    for day in days:
        for r in _lib.read_regime_day(regime_dir, day):
            for axis, col in AXES.items():
                if r.get(col, ""):
                    vals[axis].append(float(r[col]))
    return days, vals


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--regime", required=True)
    ap.add_argument("--from", dest="day_from", required=True)
    ap.add_argument("--to", dest="day_to", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    days, vals = axis_values(a.regime, a.day_from, a.day_to)
    edges_by_axis = {axis: quantile_edges(v) for axis, v in vals.items()}
    with open(a.out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["axis", "days", "minutes"] + [f"q{100 * k // BINS}" for k in range(1, BINS)])
        for axis, edges in edges_by_axis.items():
            w.writerow([axis, len(days), len(vals[axis])] + [f"{e:.2f}" for e in edges])
            print(f"{axis}: суток {len(days)}, минут {len(vals[axis])}, края "
                  f"{', '.join(f'{e:.1f}' for e in edges)} bps", file=sys.stderr)
    print(" ".join(f"{name}:{spec}" for name, spec in build_sets(edges_by_axis)))


if __name__ == "__main__":
    main()
