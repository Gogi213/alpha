#!/usr/bin/env python3
"""Г-72 (П-15 F1, TK-274): пороги T_m(lag, q) — q-квантиль хода BTC за lag секунд по ВРЕМЕНИ (каждая секунда — наблюдение)
по предыдущим полным месяцам (январь — по январю): то же правило калибровки, что `btc-thresholds.py` (Г-10, Судья 11.10).

Ряд — `--btc-seconds <каталог>` с файлами `<сутки>.csv` (`second_ms,close_ticks`, 86 400 строк; тот же вход, что у `bounce-grid`).
Ход — как `BtcSeconds::lag_bps` (sets.rs): (close[i] − close[i−lag]) / close[i−lag] · 1e4, i — последняя закрытая секунда;
обе секунды > 0 (иначе — не в долю). Секунда принадлежит месяцу своей даты UTC; окно переходит границу суток по сплошному ряду.
Квантиль — как titration-points: `sorted(v)[len(v)·q // 100]`, округление до 2 знаков.

    btc-lag-thresholds.py --btc-seconds <каталог> --out <json> [--from 2026-01] [--to 2026-09]
Выход: {"ГГГГ-ММ": {"<lag>": {"<q>": T}}}; пустой месяц/лаг пропущен.
Без numpy: ход округляется до 2 знаков при подсчёте (Counter) — округление монотонно, порядковая статистика та же, что у sorted(v)[…] с round после.
"""
import argparse
import datetime
import json
import os
import re
import sys
from collections import Counter

LAGS = (1, 2, 4, 8, 16, 32, 64, 118)
QUANTILES = (5, 10, 20, 33, 50, 67, 80, 90, 95)
DAY_S = 86_400


def read_day(path, day_ms):
    """86 400 close одних суток; формат и порядок секунд проверяются так же строго, как в Rust."""
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    if len(lines) != DAY_S + 1:
        sys.exit(f"{path}: {len(lines) - 1} строк, нужно {DAY_S}")
    out = []
    for i, ln in enumerate(lines[1:]):
        ms, _, c = ln.partition(",")
        if int(ms) != day_ms + 1000 * i:
            sys.exit(f"{path}: строка {i + 2}: секунда не по порядку")
        out.append(int(c))
    return out


def month_counts(dirp, first, last):
    """{ГГГГ-ММ: {lag: Counter(ход округл. до 2 знаков, bps -> число секунд)}}; ряд склеен по суткам, окно переходит границу суток."""
    days = sorted(m.group(1) for m in (re.fullmatch(r"(\d{4}-\d{2}-\d{2})\.csv", f) for f in os.listdir(dirp)) if m)
    ms = {d: int(datetime.datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc).timestamp()) * 1000 for d in days}
    out = {}
    prev = None  # (хвост предыдущих суток длиной max(LAGS), начало этих суток мс)
    pad = max(LAGS)
    for d in days:
        close = read_day(os.path.join(dirp, f"{d}.csv"), ms[d])
        month = d[:7]
        gap = prev is None or ms[d] - prev[1] != DAY_S * 1000
        if first <= month <= last:
            ser = close if gap else prev[0] + close
            off = 0 if gap else len(prev[0])
            slot = out.setdefault(month, {n: Counter() for n in LAGS})
            for n in LAGS:
                lo = max(off, n)  # первая секунда с окном
                cnt = slot[n]
                for c1, c0 in zip(ser[lo:], ser[lo - n:]):
                    if c1 > 0 and c0 > 0:
                        cnt[round((c1 - c0) / c0 * 1e4, 2)] += 1
        prev = (close[-pad:], ms[d])
    return out


def quantile(cnt, q):
    """sorted(v)[len(v)·q // 100] по счётчику значений."""
    total = sum(cnt.values())
    k = (total * q) // 100
    acc = 0
    for x in sorted(cnt):
        acc += cnt[x]
        if acc > k:
            return round(x, 2)
    raise ValueError("пустой счётчик")


def expanding(vals):
    months = sorted(vals)
    res = {}
    for i, m in enumerate(months):
        src = months[:i] or [m]  # январь — по январю; прочие — только прошлые полные месяцы
        res[m] = {}
        for n in LAGS:
            pool = Counter()
            for s in src:
                pool.update(vals[s][n])
            if pool:
                res[m][str(n)] = {str(q): quantile(pool, q) for q in QUANTILES}
    return res


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--btc-seconds", required=True)
    p.add_argument("--from", dest="first", default="2026-01")
    p.add_argument("--to", dest="last", default="2026-09")
    p.add_argument("--out", required=True)
    a = p.parse_args()
    res = expanding(month_counts(a.btc_seconds, a.first, a.last))
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(res, f, ensure_ascii=False, sort_keys=True, indent=1)
        f.write("\n")
    print(f"{a.out}: {len(res)} мес.", file=sys.stderr)


if __name__ == "__main__":
    main()
