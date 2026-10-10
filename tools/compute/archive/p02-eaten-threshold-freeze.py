#!/usr/bin/env python3
"""П-02, вторая волна, Г-86: заморозка порога `eaten_min` до счёта исходов.

Читает распределение `eaten_pct` (та же величина, что `--set eaten=<%>`/`eaten_min=<%>`
в движке — `100 * (1 - size_at_touch/size_max_before)`, `src/commands/lob/bounce_grid/
cache.rs:13-18`) на **августе**, только по колонкам `size_at_touch`/`size_max_before` —
`ended_by_death` в этом проходе не читается (протокол П-02, «Заморожено перед счётом»:
порог не подгоняется под исход). Печатает квантили p50/p75/p80/p90/p95/p99 по двум базам:

  - «все касания» (включая `eaten_pct=0`, когда стена не сдавала размер к касанию или
    истории размера нет вовсе — так же трактует ноль сам движок, `size_max_before<=0`);
  - «только где было что есть» (`size_max_before>0`) — распределение среди касаний,
    у которых вообще есть история пика размера.

Используется как основа выбора порога Г-86 «огромный завал уже проедается» (P-90):
не изобретать число, взять квантиль (обычно p90 — верхний дециль эрозии) реального
распределения. Само число для заморозки выбирает исполнитель по годам протокола, этот
скрипт только считает квантили — решение и число вписываются в протокол вручную.

Использование (на Steam Deck, без numpy/pandas):
    python3 p02-eaten-threshold-freeze.py --alpha-home ~/alpha/epochs/e-aug \
        --d20 study/approaches/D20 --day-from 2026-08-01 --day-to 2026-08-31
"""
from __future__ import annotations

import argparse
import csv
import glob
import os
import sys
from datetime import date, timedelta
from typing import List

POOL_EXCLUDE = {"TRXUSDT"}  # тот же excl., что p02-wall.py (В-105) — денег здесь нет, но для
                             # единообразия распределения с блоком A/счётом Г-86 держим тем же.


def daterange(start: str, end: str) -> List[str]:
    d, e = date.fromisoformat(start), date.fromisoformat(end)
    out = []
    while d <= e:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def percentile_sorted(xs: List[float], q: float):
    n = len(xs)
    if n == 0:
        return None
    if n == 1:
        return xs[0]
    import math
    k = (n - 1) * q
    f, c = math.floor(k), math.ceil(k)
    if f == c:
        return xs[int(k)]
    return xs[f] * (c - k) + xs[c] * (k - f)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--alpha-home", default="~/alpha/epochs/e-aug")
    ap.add_argument("--d20", default="study/approaches/D20")
    ap.add_argument("--day-from", default="2026-08-01")
    ap.add_argument("--day-to", default="2026-08-31")
    args = ap.parse_args()

    home = os.path.expanduser(args.alpha_home)
    root = os.path.join(home, args.d20)
    all_vals: List[float] = []
    nonzero_vals: List[float] = []
    n_touches = 0
    n_files = 0
    for day in daterange(args.day_from, args.day_to):
        day_dir = os.path.join(root, day)
        if not os.path.isdir(day_dir):
            print(f"[warn] нет {day_dir}", file=sys.stderr)
            continue
        for path in sorted(glob.glob(os.path.join(day_dir, "touches-*.csv"))):
            sym = os.path.basename(path)[len("touches-"):-len(".csv")]
            if sym in POOL_EXCLUDE:
                continue
            n_files += 1
            with open(path, newline="", encoding="utf-8") as f:
                r = csv.reader(f)
                header = next(r)
                i_at = header.index("size_at_touch")
                i_max = header.index("size_max_before")
                i_max_col = max(i_at, i_max)
                for row in r:
                    if len(row) <= i_max_col:
                        continue
                    try:
                        at = float(row[i_at])
                        mx = float(row[i_max])
                    except ValueError:
                        continue
                    n_touches += 1
                    if mx <= 0:
                        all_vals.append(0.0)
                        continue
                    eaten = 100.0 * (1.0 - at / mx)
                    all_vals.append(eaten)
                    nonzero_vals.append(eaten)
    all_vals.sort()
    nonzero_vals.sort()
    qs = [0.50, 0.75, 0.80, 0.90, 0.95, 0.99]
    print(f"файлов={n_files} касаний={n_touches} с историей пика(size_max_before>0)={len(nonzero_vals)}")
    print("квантили eaten_pct, % — все касания (ноль = нет истории пика или пик не превышен):")
    for q in qs:
        print(f"  p{int(q*100)}: {percentile_sorted(all_vals, q):.2f}")
    print("квантили eaten_pct, % — только с историей пика (size_max_before>0):")
    for q in qs:
        print(f"  p{int(q*100)}: {percentile_sorted(nonzero_vals, q):.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
