#!/usr/bin/env python3
"""Г-10 (П-14 F1, TK-189): пороги T_m(N, q) — q-квантиль хода BTC за N ч по ВРЕМЕНИ (каждая минута — наблюдение)
по предыдущим полным месяцам (январь — по январю): Судья 11.10, reviews/P-14-2026-10-11-f1-axes.md.

Ход — колонки `btc_ret_<N>h_bps` файлов режима `study/regime/<сутки>.csv` (regime.py; 6/8/12/24 ч — после TK-189,
режим нужно пересчитать). Берутся только минуты своих суток файла (файл несёт и предыдущие), пусто — не в долю.
Квантиль — как в titration-points: `sorted(v)[len(v)·q // 100]`, округление до 2 знаков (как `--set`).

    btc-thresholds.py --regime <study/regime> --out <json>
Выход: {"ГГГГ-ММ": {"<N>": {"<q>": T}}}; нет колонки/минут — месяц/окно пропущены (в мастере → причина отказа).
"""
import argparse
import csv
import json
import os
import re
import sys

WINDOWS_H = (1, 2, 3, 4, 6, 8, 12, 24)
QUANTILES = (5, 10, 20, 33, 50, 67, 80, 90, 95)
DAY_MS = 86_400_000


def month_values(regime_dir, days):
    """{N: [ход, bps]} по минутам своих суток для списка файлов суток."""
    out = {n: [] for n in WINDOWS_H}
    for day in days:
        path = os.path.join(regime_dir, f"{day}.csv")
        with open(path, encoding="utf-8", newline="") as f:
            r = csv.DictReader(f)
            cols = {n: f"btc_ret_{n}h_bps" for n in WINDOWS_H if f"btc_ret_{n}h_bps" in (r.fieldnames or [])}
            for row in r:
                minute = int(row["minute_ms"])
                if minute - minute % DAY_MS != _day_ms(day):
                    continue
                for n, c in cols.items():
                    if row[c] != "":
                        out[n].append(float(row[c]))
    return out


def _day_ms(day):
    import datetime
    y, m, d = map(int, day.split("-"))
    return int(datetime.datetime(y, m, d, tzinfo=datetime.timezone.utc).timestamp() * 1000)


def quantile(values, q):
    v = sorted(values)
    return round(v[(len(v) * q) // 100], 2)


def thresholds(regime_dir):
    days = sorted(m.group(1) for m in (re.fullmatch(r"(\d{4}-\d{2}-\d{2})\.csv", f) for f in os.listdir(regime_dir)) if m)
    by_month = {}
    for d in days:
        by_month.setdefault(d[:7], []).append(d)
    months = sorted(by_month)
    vals = {m: month_values(regime_dir, by_month[m]) for m in months}
    res = {}
    for i, m in enumerate(months):
        src = months[:i] or [m]   # январь (первый месяц) — по самому январю; прочие — только прошлые полные месяцы
        res[m] = {}
        for n in WINDOWS_H:
            pool = [x for s in src for x in vals[s][n]]
            if pool:
                res[m][str(n)] = {str(q): quantile(pool, q) for q in QUANTILES}
    return res


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--regime", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    res = thresholds(a.regime)
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(res, f, ensure_ascii=False, sort_keys=True, indent=1)
        f.write("\n")
    print(f"{a.out}: {len(res)} мес.", file=sys.stderr)


if __name__ == "__main__":
    main()
