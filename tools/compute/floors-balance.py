#!/usr/bin/env python3
"""Баланс флоров (владелец 2026-09-19: «как ты хочешь сбалансировать флоры?»): не средний ход на
сигнал, а **ожидаемый ход в день** = сигналов/день × (markout за час − круг комиссий). Матрица
сила ×поток × возраст с постановки по касаниям `lob touches` (В-66 трекер, все колонки), плюс
фронтир по монетам. Это дешёвая разведка (секунды) для выбора 2–3 сочетаний под сетку форм —
не вердикт: без стопов, без «занято», без интервалов.

    python3 tools/compute/floors-balance.py study/touches6   # каталог touches-<SYM>.csv
"""
import collections
import csv
import glob
import os
import statistics
import sys

FEES = 4.41  # круг комиссий по рынку (В-63)
S = [0, 10, 25, 50, 100]      # сила ×поток, %
A = [0, 15, 30, 45, 60]       # возраст с постановки, мин


def load(d):
    rows = []
    for f in glob.glob(os.path.join(d, "touches-*.csv")):
        sym = os.path.basename(f)[8:-4]
        for r in csv.DictReader(open(f, encoding="utf-8")):
            try:
                rows.append((sym, r["day_utc"], float(r["strength_flow_pct"]),
                             float(r["age_ms"]) / 60000.0, float(r["m_1h"]), float(r["m_10m"])))
            except (ValueError, KeyError):
                pass
    return rows


def main():
    rows = load(sys.argv[1])
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
                m = statistics.mean(r[4] for r in sel)
                line += f"{n:8.0f}/d {m:+6.1f} {(m - FEES) * n:+8.0f}  "
            else:
                line += f"{'-':>26}"
        print(line)
    for (s, a) in [(100, 0), (50, 0), (25, 15), (10, 45), (10, 15)]:
        agg = collections.defaultdict(list)
        for r in rows:
            if r[2] >= s and r[3] >= a:
                agg[r[0]].append(r[4])
        coins = [(c, len(v) / days, statistics.mean(v)) for c, v in agg.items() if len(v) >= 6]
        coins.sort(key=lambda x: -(x[2] - FEES) * x[1])
        pos = sum(1 for c in coins if c[2] > FEES)
        print(f"== strength>={s}% age>={a}m: coins>=6 touches {len(coins)}, of them m_1h>fees {pos}; "
              "top by (m-fees)*n/day: " + ", ".join(f"{c} {n:.0f}/d {m:+.0f}" for c, n, m in coins[:8]))
        print("   bottom: " + ", ".join(f"{c} {n:.0f}/d {m:+.0f}" for c, n, m in coins[-4:]))


if __name__ == "__main__":
    main()
