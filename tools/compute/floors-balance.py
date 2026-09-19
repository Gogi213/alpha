#!/usr/bin/env python3
"""Баланс флоров (владелец 2026-09-19: «как ты хочешь сбалансировать флоры?»): не средний ход на
сигнал, а **ожидаемый ход в день** = сигналов/день × (markout за час − круг комиссий). Матрица
сила ×поток × возраст с постановки по касаниям `lob touches` (все колонки), плюс фронтир по монетам.
Это дешёвая разведка (секунды) для выбора 2–3 сочетаний под сетку форм — не вердикт: без стопов,
без «занято», без интервалов.

    python3 tools/compute/floors-balance.py study/touches/<сутки>              # матрица + фронтир
    python3 tools/compute/floors-balance.py study/touches --by-coin --csv study/floors-by-coin.csv

`--by-coin` (H2 шаг 1, handoff-2026-09-20.md): по каждой монете и каждым суткам — сигналов/день,
ход за 10 мин и за час, доля отскока (ход за час > 0) на порогах возраста {15, 30, 45, 60, 90, 120}
мин. Абсолютные числа возраста читать только из ночного H3 (`study/touches/<сутки>/`, трекер
`notional $10k`): прогоны с `--h3-mode floor` несравнимы, порог там свой на монету.
"""
import argparse
import collections
import csv
import glob
import os
import statistics
import sys

FEES = 4.41  # круг комиссий по рынку (В-63)
S = [0, 10, 25, 50, 100]          # сила ×поток, %
A = [0, 15, 30, 45, 60]           # возраст с постановки, мин (сетка матрицы)
A_COIN = [15, 30, 45, 60, 90, 120]  # H2 шаг 1: пороги оси возраста


def load(d):
    """(монета, сутки, сила ×поток %, возраст мин, m_10m, m_1h) по всем `touches-*.csv` каталога."""
    rows = []
    for f in glob.glob(os.path.join(d, "touches-*.csv")):
        sym = os.path.basename(f)[8:-4]
        for r in csv.DictReader(open(f, encoding="utf-8")):
            try:
                rows.append((sym, r["day_utc"], float(r["strength_flow_pct"]),
                             float(r["age_ms"]) / 60000.0, float(r["m_10m"]), float(r["m_1h"])))
            except (ValueError, KeyError):
                pass
    return rows


def mean(xs):
    # `xs` приходит и генератором: `if xs` у него всегда истинно, поэтому материализуем —
    # иначе пустая выборка роняет `statistics.mean` (StatisticsError) вместо нуля.
    xs = list(xs)
    return statistics.mean(xs) if xs else 0.0


def matrix(rows):
    days = len(set(r[1] for r in rows))
    print(f"touches {len(rows)} days {days} coins {len(set(r[0] for r in rows))}")
    print("cell = touches/day | mean m_1h bps | (m_1h-4.41)*touches/day  (ожидаемый ход в день, грубо)")
    print("strength\\age " + "".join(f"{a:>26}" for a in A))
    for s in S:
        line = f"{s:>10}%  "
        for a in A:
            sel = [r for r in rows if r[2] >= s and r[3] >= a]
            n = len(sel) / days
            if sel:
                m = mean(r[5] for r in sel)
                line += f"{n:8.0f}/d {m:+6.1f} {(m - FEES) * n:+8.0f}  "
            else:
                line += f"{'-':>26}"
        print(line)
    for (s, a) in [(100, 0), (50, 0), (25, 15), (10, 45), (10, 15)]:
        agg = collections.defaultdict(list)
        for r in rows:
            if r[2] >= s and r[3] >= a:
                agg[r[0]].append(r[5])
        coins = [(c, len(v) / days, mean(v)) for c, v in agg.items() if len(v) >= 6]
        coins.sort(key=lambda x: -(x[2] - FEES) * x[1])
        pos = sum(1 for c in coins if c[2] > FEES)
        print(f"== strength>={s}% age>={a}m: coins>=6 touches {len(coins)}, of them m_1h>fees {pos}; "
              "top by (m-fees)*n/day: " + ", ".join(f"{c} {n:.0f}/d {m:+.0f}" for c, n, m in coins[:8]))
        print("   bottom: " + ", ".join(f"{c} {n:.0f}/d {m:+.0f}" for c, n, m in coins[-4:]))


def by_coin(rows, csv_path, min_per_day):
    """H2 шаг 1: пороги возраста по монете и по суткам (сигналов/день, ход, доля отскока)."""
    days = sorted({r[1] for r in rows})
    nd = len(days) or 1
    print(f"== по монете: суток {nd} ({days[0]}…{days[-1]}), монет {len(set(r[0] for r in rows))}, "
          f"порог «монета считается монетой» — {min_per_day} касаний/сутки")
    table = []
    for a in A_COIN:
        sel = [r for r in rows if r[3] >= a]
        cells = collections.defaultdict(list)
        for r in sel:
            cells[(r[1], r[0])].append(r)
        coins = collections.defaultdict(list)
        for (day, sym), rs in cells.items():
            coins[sym].append((day, rs))
            table.append((day, sym, a, len(rs), mean(r[4] for r in rs), mean(r[5] for r in rs),
                          sum(1 for r in rs if r[5] > 0) / len(rs) if rs else 0.0,
                          mean(r[2] for r in rs)))
        thick = {c: v for c, v in coins.items()
                 if sum(len(rs) for _, rs in v) / nd >= min_per_day}
        ranked = sorted(thick.items(), key=lambda kv: -(mean([r[5] for _, rs in kv[1] for r in rs])
                                                        - FEES) * (sum(len(rs) for _, rs in kv[1]) / nd))
        pool_m = mean(r[5] for r in sel)
        pool_share = sum(1 for r in sel if r[5] > 0) / len(sel) if sel else 0.0
        print(f"-- порог возраста {a} мин: сигналов/сутки {len(sel)/nd:.0f}, ход за час {pool_m:+.2f} bps, "
              f"доля отскока {pool_share:.0%}, монет ≥ порога {len(thick)}")
        print("   лучшие по (ход−комиссии)×сигналов/день: "
              + ", ".join(f"{c} {sum(len(rs) for _, rs in v)/nd:.0f}/d "
                          f"{mean([r[5] for _, rs in v for r in rs]):+.0f}" for c, v in ranked[:8]))
        print("   худшие: " + ", ".join(f"{c} {sum(len(rs) for _, rs in v)/nd:.0f}/d "
                                        f"{mean([r[5] for _, rs in v for r in rs]):+.0f}"
                                        for c, v in ranked[-4:]))
    if csv_path:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["day_utc", "symbol", "age_min", "n", "mean_m_10m_bps", "mean_m_1h_bps",
                        "bounce_share", "mean_strength_flow_pct"])
            for row in sorted(table):
                w.writerow([row[0], row[1], row[2], row[3], f"{row[4]:.3f}", f"{row[5]:.3f}",
                            f"{row[6]:.4f}", f"{row[7]:.2f}"])
        print(f"csv: {csv_path} ({len(table)} строк)")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("touches", help="каталог с touches-<SIM>.csv (одни сутки или все)")
    p.add_argument("--by-coin", action="store_true", help="пороги возраста по монете и суткам (H2 шаг 1)")
    p.add_argument("--min-per-day", type=int, default=10, help="порог «монета считается монетой», касаний/сутки")
    p.add_argument("--csv", help="куда выгрузить по-монетную таблицу (day × symbol × порог)")
    a = p.parse_args()
    rows = load(a.touches)
    if not rows:
        # Пустой каталог (нет `touches-*.csv` или все пустые) — матрица не считается: без этой
        # ветки деление на нуль суток роняет скрипт трейсбеком в study/floors-<сутки>.txt.
        print("touches: строк нет — матрица не считается")
        return 0
    matrix(rows)
    if a.by_coin:
        by_coin(rows, a.csv, a.min_per_day)
    elif a.csv:
        by_coin(rows, a.csv, a.min_per_day)
    return 0


if __name__ == "__main__":
    sys.exit(main())
