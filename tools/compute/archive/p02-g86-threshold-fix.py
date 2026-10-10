#!/usr/bin/env python3
"""П-02, Г-86 (H9): пересчёт порога `eaten_min` на ПРАВИЛЬНОЙ популяции — не
на всех касаниях (как в `p02-eaten-threshold-freeze.py`), а на тех, что
реально проходят базовый фильтр набора `t-bid-btc4h-q1` (T-10, Инженер):
E1/В-66 «стена держит порог $10k в момент касания» (`TouchFilter::admits`,
`src/lob/levels.rs:271` `holds_at_touch` = `size_at_touch` даёт номинал ≥
`--h3-usd` по формуле `notional_lots_at`, `src/lob/levels.rs:209-217`) —
именно ЭТОТ гейт (не eaten) зануляет пересечение с `eaten_min=99`, потому что
уровень, всё ещё держащий $10k при касании, почти не может быть съеден на
99%+ от своего пикового размера.

Без чтения исхода (`ended_by_death`) — только распределение `eaten_pct` на
отфильтрованной популяции. Источник данных — существующий кэш D20
(`touches-<SYM>.csv`), новый счёт движком не нужен (лёгкий питон-проход).

Формула номинала — как в движке (`H3Mode::Notional::passes_birth`,
`size_lots >= ceil(min_usd / (price_tick * tick_size * qty_step))`), здесь
проверяется эквивалентно: `price_tick * tick_size * size_at_touch *
qty_step >= min_usd`.

Использование:
    python3 p02-g86-threshold-fix.py \
        --touches-dirs epochs/e-aug/study/approaches/D20/2026-08-01,... \
        --instruments epochs/e-aug/study/root-2026-08-15/instruments.csv \
        --regime-dir epochs/e-aug/study/regime \
        --min-usd 10000 --age-ms 2700000 --btc4h-max -44.55
"""
from __future__ import annotations

import argparse
import bisect
import csv
import glob
import os
from typing import Dict, Tuple


def load_instruments(path: str) -> Dict[str, Tuple[float, float]]:
    out = {}
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            try:
                out[row["symbol"]] = (float(row["tick_size"]), float(row["qty_step"]))
            except (KeyError, ValueError):
                continue
    return out


def load_regime_day(path: str):
    """(minute_ms отсортирован, btc_ret_4h_bps) для одних суток; пусто, если файла нет."""
    if not os.path.exists(path):
        return [], []
    ms, vals = [], []
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            v = row.get("btc_ret_4h_bps")
            if not v:
                continue
            ms.append(int(row["minute_ms"]))
            vals.append(float(v))
    order = sorted(range(len(ms)), key=lambda i: ms[i])
    return [ms[i] for i in order], [vals[i] for i in order]


def btc4h_at(ms_list, val_list, ts_ms: int):
    if not ms_list:
        return None
    i = bisect.bisect_right(ms_list, ts_ms) - 1
    if i < 0:
        return None
    return val_list[i]


def percentile_sorted(sorted_vals, q):
    n = len(sorted_vals)
    if n == 0:
        return None
    k = (n - 1) * q
    f, c = int(k), min(int(k) + 1, n - 1)
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def eaten_pct(size_at_touch: float, size_max_before: float) -> float:
    if size_max_before <= 0:
        return 0.0
    return 100.0 * (1.0 - size_at_touch / size_max_before)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--touches-dirs", required=True, help="через запятую — каталоги D20 по суткам")
    ap.add_argument("--instruments", required=True)
    ap.add_argument("--regime-dir", required=True)
    ap.add_argument("--min-usd", type=float, default=10000.0)
    ap.add_argument("--age-ms", type=int, default=2_700_000)
    ap.add_argument("--btc4h-max", type=float, default=-44.55)
    # В-105 / П-02 §4: пул без TRX (правка по Судье 26.09, reviews/P-02-2026-09-26-amend-g86.md)
    ap.add_argument("--drop", default="TRXUSDT", help="монеты вне пула через запятую (умолчание — TRXUSDT, В-105)")
    ap.add_argument("--count-ge", type=float, default=None,
                    help="напечатать число касаний P2 с eaten_pct ≥ порога (ожидаемые сигналы, без исходов)")
    args = ap.parse_args()

    instruments = load_instruments(args.instruments)
    regime_cache: Dict[str, tuple] = {}

    eaten_p1 = []  # holds$10k ∧ age ∧ bid (без btc4h)
    eaten_p2 = []  # + btc4h_max

    drop = {x for x in args.drop.split(",") if x}
    dirs = args.touches_dirs.split(",")
    for d in dirs:
        day = os.path.basename(d.rstrip("/"))
        ms_list, val_list = regime_cache.get(day, (None, None))
        if ms_list is None:
            ms_list, val_list = load_regime_day(os.path.join(args.regime_dir, f"{day}.csv"))
            regime_cache[day] = (ms_list, val_list)
        for path in sorted(glob.glob(os.path.join(d, "touches-*.csv"))):
            sym = os.path.basename(path)[len("touches-") : -len(".csv")]
            if sym in drop:
                continue
            inst = instruments.get(sym)
            if inst is None:
                continue
            tick_size, qty_step = inst
            with open(path, encoding="utf-8", newline="") as f:
                first = f.readline()
                if not first.startswith("#"):
                    f.seek(0)
                for row in csv.DictReader(f):
                    try:
                        side = row["side"]
                        if side != "bid":
                            continue
                        age_ms = int(row["age_ms"])
                        if age_ms < args.age_ms:
                            continue
                        price_tick = int(row["price_tick"])
                        size_at_touch = float(row["size_at_touch"])
                        size_max_before = float(row["size_max_before"])
                        start_ms = int(row["start_ms"])
                    except (KeyError, ValueError):
                        continue
                    notional_usd = price_tick * tick_size * size_at_touch * qty_step
                    if notional_usd < args.min_usd:
                        continue
                    ep = eaten_pct(size_at_touch, size_max_before)
                    eaten_p1.append(ep)
                    btc4h = btc4h_at(ms_list, val_list, start_ms)
                    if btc4h is not None and btc4h <= args.btc4h_max:
                        eaten_p2.append(ep)

    eaten_p1.sort()
    eaten_p2.sort()
    print(f"P1 (holds ${args.min_usd:.0f} ∧ age>={args.age_ms}мс ∧ bid), n={len(eaten_p1)}:")
    for q in (0.5, 0.75, 0.9, 0.95, 0.99):
        print(f"  p{int(q*100)}={percentile_sorted(eaten_p1, q)}")
    print(f"P2 (+ btc4h_ret_4h_bps<={args.btc4h_max}), n={len(eaten_p2)}:")
    for q in (0.5, 0.75, 0.9, 0.95, 0.99):
        print(f"  p{int(q*100)}={percentile_sorted(eaten_p2, q)}")
    n_ge99_p1 = sum(1 for x in eaten_p1 if x >= 99.0)
    n_ge99_p2 = sum(1 for x in eaten_p2 if x >= 99.0)
    print(f"eaten_pct>=99: P1={n_ge99_p1}/{len(eaten_p1)} P2={n_ge99_p2}/{len(eaten_p2)}")
    if args.count_ge is not None:
        n_ge = sum(1 for x in eaten_p2 if x >= args.count_ge)
        print(f"P2 с eaten_pct>={args.count_ge}: {n_ge} касаний (верхняя граница сигналов H9, без исходов)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
