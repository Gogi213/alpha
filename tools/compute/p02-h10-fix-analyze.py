#!/usr/bin/env python3
"""П-02, вторая волна: досчёт H10 (Г-105) на сентябре — обновление блока B после того, как
недостающие сутки (2026-09-05, 09-14, 09-15) досчитаны (см. `p02-block-b-2026-09-26.md`,
«Отступления» п.1).

Читает JSON `tools/compute/portfolio-sim.py --json` (два варианта — база и h10-before, эпохи
история+запись), режет `daily` каждого варианта до `2026-09-01..2026-09-23` **вручную** (сама
`portfolio-sim.py` дней не ограничивает — `b5/titrc-u500r` продолжает копиться ночной сеткой,
это и объясняло расхождение $103,46 против $114,58 в блоке B, см. тот отчёт), затем считает Δ$
по суткам в паре, кластерный бутстреп (метод — как в блоке B: 20000 передискретизаций, 95% CI
перцентилем, p из нормального приближения по бутстреп-SE) и эффективное N
(`effective_n_from_autocorr`, `.claude/skills/alpha-research/scripts/effective_n.py`, портировано
на чистый python — на Steam Deck нет numpy), капированное сверху числом календарных суток.

Использование:
    python3 p02-h10-fix-analyze.py --in /tmp/p02c-h10-sept.json --day-to 2026-09-23 \
        --base-variant база --cell-variant h10 --seed 20260926
"""
from __future__ import annotations

import argparse
import json
import math
import random
from typing import Dict, List, Optional


def effective_n_from_autocorr(returns: List[float]) -> float:
    r = [x for x in returns if x is not None and not math.isnan(x)]
    n = len(r)
    if n < 8:
        return float(n)
    max_lag = max(1, min(n // 4, int(4 * (n / 100.0) ** (2.0 / 9.0)) + 10))
    mean = sum(r) / n
    x = [v - mean for v in r]
    denom = sum(v * v for v in x)
    if denom <= 0:
        return float(n)
    total = 0.0
    for k in range(1, max_lag + 1):
        num = sum(x[i] * x[i + k] for i in range(n - k))
        rho = num / denom
        total += (1.0 - k / n) * rho
    factor = 1.0 + 2.0 * total
    if factor < 1.0:  # оценка больше n — потолок n (как в навыке, Судья b8f2988)
        return float(n)
    return max(1.0, n / factor)


def merged_daily(grid: List[dict], variant: str, day_to: str) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for row in grid:
        if row["variant"] != variant:
            continue
        if row["epoch"] not in ("история", "запись"):
            continue
        for day, v in row.get("daily", {}).items():
            if day > day_to:
                continue
            out[day] = out.get(day, 0.0) + v
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--day-from", default="2026-09-01")
    ap.add_argument("--day-to", default="2026-09-23")
    ap.add_argument("--base-variant", default="база")
    ap.add_argument("--cell-variant", default="h10")
    ap.add_argument("--n-boot", type=int, default=20000)
    ap.add_argument("--seed", type=int, default=20260926)
    args = ap.parse_args()

    with open(args.inp, encoding="utf-8") as f:
        data = json.load(f)
    grid = data["grid"]

    base_daily = merged_daily(grid, args.base_variant, args.day_to)
    cell_daily = merged_daily(grid, args.cell_variant, args.day_to)
    base_daily = {d: v for d, v in base_daily.items() if d >= args.day_from}
    cell_daily = {d: v for d, v in cell_daily.items() if d >= args.day_from}

    base_total = sum(base_daily.values())
    cell_total = sum(cell_daily.values())
    print(f"база {args.day_from}..{args.day_to}: суток={len(base_daily)} сумма=${base_total:.2f}")
    print(f"{args.cell_variant} {args.day_from}..{args.day_to}: суток={len(cell_daily)} сумма=${cell_total:.2f}")

    all_days = sorted(set(base_daily) | set(cell_daily))
    deltas = [cell_daily.get(d, 0.0) - base_daily.get(d, 0.0) for d in all_days]
    n_days = len(all_days)
    delta_total = sum(deltas)
    print(f"дней в объединении={n_days} (совпадение с деньгами: база−клетка по общим суткам)")
    print(f"Δ$ (сумма по суткам) = {delta_total:.2f}")

    rng = random.Random(args.seed)
    boot = []
    for _ in range(args.n_boot):
        s = 0.0
        for _ in range(n_days):
            s += deltas[rng.randrange(n_days)]
        boot.append(s)
    boot.sort()
    mean_boot = sum(boot) / len(boot)
    var_boot = sum((b - mean_boot) ** 2 for b in boot) / (len(boot) - 1)
    se = math.sqrt(var_boot)
    ci_lo = boot[int(0.025 * len(boot))]
    ci_hi = boot[int(0.975 * len(boot))]
    z = delta_total / se if se > 0 else 0.0
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))

    n_eff_raw = effective_n_from_autocorr(deltas)
    n_eff = None if n_eff_raw is None else min(n_eff_raw, float(n_days))

    print(f"95% CI (бутстреп по суткам, {args.n_boot} передискретизаций) = [{ci_lo:.2f}; {ci_hi:.2f}]")
    print(f"p (нормальное приближение по бутстреп-SE) = {p:.4f}")
    if n_eff is None:
        print(f"эфф. N = не определено (оценка больше n, календарных суток {n_days})")
    else:
        print(f"эфф. N = {n_eff:.1f} (сырое {n_eff_raw:.1f}, календарных суток {n_days})")


if __name__ == "__main__":
    raise SystemExit(main())
