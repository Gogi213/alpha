#!/usr/bin/env python3
"""Г-10 (П-14 F1, TK-189): пороги T_m(N, q) — q-квантиль хода BTC за N ч по ВРЕМЕНИ (каждая минута — наблюдение)
по предыдущим полным месяцам (январь — по январю): Судья 11.10, reviews/P-14-2026-10-11-f1-axes.md.

Ход — колонки `btc_ret_<N>h_bps` файлов режима `study/regime/<сутки>.csv` (regime.py; 6/8/12/24 ч — после TK-189,
режим нужно пересчитать). Берутся только минуты своих суток файла (файл несёт и предыдущие), пусто — не в долю.
Квантиль — как в titration-points: `sorted(v)[len(v)·q // 100]`, округление до 2 знаков (как `--set`).

    btc-thresholds.py --regime <study/regime> --out <json>
    btc-thresholds.py --ref <ref-BTCUSDT-1m.csv> [--ref …] --from 2026-01 --out <json>

Режим `--ref`: те же ходы напрямую из минутных свечей BTC (формула `regime.py:ret_bps` — от закрытия минуты m−N·60−1 к закрытию m−1,
без заглядывания), каждая минута месяца — наблюдение; не требует пересчёта режима (файлы `--ref` склеиваются по минуте).
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


def quantile(values, q, presorted=False):
    v = values if presorted else sorted(values)
    return round(v[(len(v) * q) // 100], 2)


def thresholds(regime_dir):
    days = sorted(m.group(1) for m in (re.fullmatch(r"(\d{4}-\d{2}-\d{2})\.csv", f) for f in os.listdir(regime_dir)) if m)
    by_month = {}
    for d in days:
        by_month.setdefault(d[:7], []).append(d)
    months = sorted(by_month)
    return expanding({m: month_values(regime_dir, by_month[m]) for m in months})


def ref_values(ref_files, first_month):
    """{ГГГГ-ММ: {N: [ход, bps]}} по минутам месяцев ≥ first_month из рядов close (формула regime.ret_bps)."""
    import datetime
    close = {}
    for fn in ref_files:
        with open(fn, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                close[int(row["minute_ms"])] = float(row["close"])
    out = {}
    for minute in sorted(close):
        m = datetime.datetime.fromtimestamp(minute / 1000, datetime.timezone.utc).strftime("%Y-%m")
        if m < first_month:
            continue
        now = close.get(minute - 60_000)
        if now is None:
            continue
        slot = out.setdefault(m, {n: [] for n in WINDOWS_H})
        for n in WINDOWS_H:
            then = close.get(minute - (n * 60 + 1) * 60_000)
            if then is not None and then > 0:
                slot[n].append((now / then - 1.0) * 1e4)
    return out


def expanding(vals):
    months = sorted(vals)
    res = {}
    for i, m in enumerate(months):
        src = months[:i] or [m]   # январь (первый месяц) — по самому январю; прочие — только прошлые полные месяцы
        res[m] = {}
        for n in WINDOWS_H:
            pool = [x for s in src for x in vals[s][n]]
            if pool:
                pool.sort()   # одна сортировка на пул; результат тот же, что sorted() на каждый q
                res[m][str(n)] = {str(q): quantile(pool, q, True) for q in QUANTILES}
    return res


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--regime")
    p.add_argument("--ref", action="append", default=[])
    p.add_argument("--from", dest="first", default="2026-01")
    p.add_argument("--out", required=True)
    p.add_argument("--all-q", action="store_true", help="все целые q 1..99 (П-15 F2: разрешимость 1); без флага — прежние 9")
    a = p.parse_args()
    if a.all_q:
        global QUANTILES
        QUANTILES = tuple(range(1, 100))
    if bool(a.regime) == bool(a.ref):
        sys.exit("нужен ровно один из --regime / --ref")
    res = expanding(ref_values(a.ref, a.first)) if a.ref else thresholds(a.regime)
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(res, f, ensure_ascii=False, sort_keys=True, indent=1)
        f.write("\n")
    print(f"{a.out}: {len(res)} мес.", file=sys.stderr)


if __name__ == "__main__":
    main()
