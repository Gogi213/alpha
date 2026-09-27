#!/usr/bin/env python3
"""Судья 27.09: независимый пересчёт σ₂₄₀ (В-131) из сырых минутных свечей против study/sigma240.

Своя реализация (прямой цикл по окну, без префиксных сумм): для случайных строк таблицы — σ заново;
для случайных минут без строки — проверка, что доходностей < 200. Только чтение.
    python3 sigma-recheck.py <alpha> SYM1,SYM2,… [n_rows]
"""
import csv, math, os, random, sys

A = sys.argv[1]
SYMS = sys.argv[2].split(",")
N = int(sys.argv[3]) if len(sys.argv) > 3 else 300
DIRS = [f"{A}/epochs/e-aug/study/klines", f"{A}/study/klines", f"{A}/study/klines-0831"]
M = 60_000
random.seed(20260927)
worst = 0.0
for s in SYMS:
    close = {}
    for d in DIRS:
        p = f"{d}/ref-{s}-1m.csv"
        if os.path.exists(p):
            for r in csv.DictReader(open(p)):
                close[int(r["minute_ms"])] = float(r["close"])
    tab = {}
    for r in csv.DictReader(open(f"{A}/study/sigma240/sigma-{s}.csv")):
        tab[int(r["window_end_ms"])] = float(r["sigma_bps"])
    def direct(E):
        ss, n = 0.0, 0
        for k in range(1, 241):
            m = E - k * M                      # минута с открытием m закрыта к E
            if m in close and (m - M) in close:
                ss += math.log(close[m] / close[m - M]) ** 2
                n += 1
        return n, math.sqrt(ss) * 1e4
    keys = sorted(tab)
    rows = random.sample(keys, min(N, len(keys)))
    dmax = 0.0
    for E in rows:
        n, sg = direct(E)
        assert n >= 200, (s, E, n)
        dmax = max(dmax, abs(sg - tab[E]))
    lo, hi = min(close), max(close) + M
    miss = [E for E in range(lo + 200 * M, hi + M, M) if E not in tab]
    bad_miss = sum(1 for E in random.sample(miss, min(N, len(miss))) if direct(E)[0] >= 200)
    gaps = sum(1 for m in range(lo, hi, M) if m not in close)
    worst = max(worst, dmax)
    print(f"{s}: строк {len(tab)}, свечей {len(close)}, дыр {gaps}, сверено {len(rows)}: max|Δσ| {dmax:.2e} bps; "
          f"без строки {len(miss)}, из них с ≥200 доходностями {bad_miss}")
print(f"ИТОГ max|Δσ| {worst:.2e} bps")
