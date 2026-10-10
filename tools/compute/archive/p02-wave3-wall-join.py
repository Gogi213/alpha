#!/usr/bin/env python3
"""П-02, третья очередь: Г-33 (декоративная цепочка `repriced` без роста
`traded_lots`) и Г-36 (айсберг — частичный снос + `traded_lots` за жизнь
намного больше пикового видимого размера) — на лету, из сырого
`levels-<SYM>.csv` (одна строка на уровень) + касаний из кэша D20 (одна
строка на касание), без хранения сырых уровней дольше одного символа-суток.

Пороги (заморожены ДО чтения `ended_by_death`/`outcome`, коммит
`docs/research/P-02-batch-wall-entry.md` "Заморожено... третья очередь"):
  --g33-max-traded  0   — декоративный кандидат: repriced=true И traded_lots
                          <= порог (0 — определение, не квантиль: «без роста»
                          читается буквально как «не было реальной торговли»)
  --g36-ratio       из распределения traded_lots/size_max среди
                    size_monotonic=false на выборке августа (см. отчёт
                    заморозки) — не выдумано, посчитано один раз, без исхода

Цепочка Г-33: уровни группируются по (side, price_tick), сортируются по
birth_ms; декоративные кандидаты, идущие ПОДРЯД в этой сортировке (без
недекоративного уровня между ними), образуют цепочку; длина цепочки
приписывается каждому её члену. «Подряд» — это соседство по возникновению на
той же цене, не по времени в абсолютных мс (между уровнями на одной цене
могут быть уровни на других ценах — это не считается разрывом).

Выход — одна строка на касание:
  symbol,day_utc,side,price_tick,touch_index,ended_by_death,
  g33_chain2,g33_chain3,g36_iceberg

Использование (один символ-сутки):
    python3 p02-wave3-wall-join.py --symbol CRVUSDT --day-utc 2026-08-01 \
        --levels /tmp/levels-CRVUSDT.csv \
        --touches study/approaches/D20/2026-08-01/touches-CRVUSDT.csv \
        --out /tmp/compact-CRVUSDT.csv --g33-max-traded 0 --g36-ratio 12.0
"""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from typing import Dict, List, Tuple


def load_levels(path: str) -> Dict[Tuple[str, str, str], dict]:
    """(side, price_tick, birth_ms) -> строка уровня (raw dict)."""
    by_key: Dict[Tuple[str, str, str], dict] = {}
    groups: Dict[Tuple[str, str], List[dict]] = defaultdict(list)
    with open(path, encoding="utf-8", newline="") as f:
        first = f.readline()
        if not first.startswith("#"):
            f.seek(0)
        r = csv.DictReader(f)
        for row in r:
            key = (row["side"], row["price_tick"], row["birth_ms"])
            by_key[key] = row
            groups[(row["side"], row["price_tick"])].append(row)
    return by_key, groups


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbol", required=True)
    p.add_argument("--day-utc", required=True)
    p.add_argument("--levels", required=True)
    p.add_argument("--touches", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--g33-max-traded", type=float, default=0.0)
    p.add_argument("--g36-ratio", type=float, required=True)
    args = p.parse_args(argv)

    by_key, groups = load_levels(args.levels)

    # --- Г-33: цепочки декоративных уровней по (side, price_tick), сорт. birth_ms ---
    chain_len: Dict[Tuple[str, str, str], int] = {}
    for (side, price_tick), rows in groups.items():
        rows.sort(key=lambda r: int(r["birth_ms"]))
        run: List[dict] = []
        for row in rows:
            decorative = (
                row["repriced"].strip().lower() == "true"
                and float(row["traded_lots"]) <= args.g33_max_traded
            )
            if decorative:
                run.append(row)
            else:
                if len(run) >= 2:
                    for r in run:
                        chain_len[(r["side"], r["price_tick"], r["birth_ms"])] = len(run)
                run = []
        if len(run) >= 2:
            for r in run:
                chain_len[(r["side"], r["price_tick"], r["birth_ms"])] = len(run)

    # --- Г-36: айсберг-флаг прямо на уровне (не требует цепочки) ---
    def iceberg_flag(row: dict) -> bool:
        if row["size_monotonic"].strip().lower() != "false":
            return False
        size_max = max(float(row["size_max"]), 1.0)
        ratio = float(row["traded_lots"]) / size_max
        return ratio >= args.g36_ratio

    with open(args.touches, encoding="utf-8", newline="") as f, open(
        args.out, "a", encoding="utf-8", newline=""
    ) as out:
        first = f.readline()
        if not first.startswith("#"):
            f.seek(0)
        r = csv.DictReader(f)
        for row in r:
            key = (row["side"], row["price_tick"], row["birth_ms"])
            lvl = by_key.get(key)
            cl = chain_len.get(key, 0)
            g33_2 = 1 if cl >= 2 else 0
            g33_3 = 1 if cl >= 3 else 0
            g36 = 1 if (lvl is not None and iceberg_flag(lvl)) else 0
            out.write(
                ",".join(
                    [
                        args.symbol,
                        args.day_utc,
                        row["side"],
                        row["price_tick"],
                        row["touch_index"],
                        row["ended_by_death"],
                        str(g33_2),
                        str(g33_3),
                        str(g36),
                    ]
                )
                + "\n"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
