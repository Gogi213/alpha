#!/usr/bin/env python3
"""П-02, блок B, H9 (Г-86, поправка §12, В-113): рыночный вход против лимитного на тех же касаниях
(`eaten_min=89.9`, `p02-stage2-h9-market.sh`), Δ$/месяц = рынок − лимит, парно по суткам — метод блока B
(`p02-h10-fix-analyze.py`): счёт $2500 / позиция $500 (`portfolio-sim.simulate`: одна позиция на монету, без
потолка, стопа дня, выключателя), деньги по суткам закрытия, сутки — объединение суток с деньгами у любой клетки,
бутстреп по суткам (95 % перцентилем, p — нормальное приближение по бутстреп-SE), эфф. N — порт
`effective_n_from_autocorr` (оценка больше n — «не определено», В-113). Без TRX (В-105). Чистый python
(Steam Deck без numpy). Направление H9: Δ > 0 — «в сторону гипотезы».

    python3 tools/compute/p02-g86-analyze.py --klines study/klines --klines epochs/e-aug/study/klines \\
        --out study/p02-g86-results.json
"""
from __future__ import annotations

import argparse
import csv
import glob
import importlib.util
import json
import math
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))
SET = "t-bid-btc4h-q1"
CELLS = {  # месяц → (лимит, рынок)
    "август": ("b5/p02-h9e899-aug-limit", "b5/p02-h9e899-aug-market"),
    "сентябрь": ("b5/p02-h9e899-sep-limit", "b5/p02-h9e899-sep-market"),
}
MONTH_DAYS = {"август": ("2026-08-01", "2026-08-31"), "сентябрь": ("2026-09-01", "2026-09-23")}


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, fname))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def forms_of(home, run):
    """Имя формы из rounds.csv прогона (одна форма на клетку); None — сделок нет ни в одних сутках."""
    seen = set()
    for f in glob.glob(os.path.join(home, run, "20*", SET, "rounds.csv")):
        with open(f, encoding="utf-8") as fh:
            for r in csv.DictReader(line for line in fh if not line.startswith("#")):
                seen.add(r["form"])
    if len(seen) > 1:
        raise SystemExit(f"{run}: больше одной формы {sorted(seen)}")
    return next(iter(seen), None)


def cell_days(home, run):
    return sorted(os.path.basename(p)[:-5] for p in glob.glob(os.path.join(home, run, "20*.done")))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--home", default=".")
    ap.add_argument("--klines", action="append", default=[])
    ap.add_argument("--drop", default="TRXUSDT")
    ap.add_argument("--n-boot", type=int, default=20000)
    ap.add_argument("--seed", type=int, default=20260926)
    ap.add_argument("--out")
    a = ap.parse_args()
    ps = _load("psim", "portfolio-sim.py")
    en = _load("h10fix", "p02-h10-fix-analyze.py")
    klines = ps.Klines(a.klines)
    drop = set(x for x in a.drop.split(",") if x)

    res = {}
    for month, (lim_run, mkt_run) in CELLS.items():
        lo, hi = MONTH_DAYS[month]
        daily, info = {}, {}
        for cell, run in (("лимит", lim_run), ("рынок", mkt_run)):
            days = [d for d in cell_days(a.home, run) if lo <= d <= hi]
            form = forms_of(a.home, run)
            rows = [r for r in ps.load_run(a.home, run, SET, form) if r["sym"] not in drop] if form else []
            sim = ps.simulate(rows, ([], []), klines, 2500.0, 0, 0.0, 0, set(), 59.7, 0)
            daily[cell] = sim["daily"]
            info[cell] = {"run": run, "form": form, "days_done": len(days), "rounds": len(rows), "taken": sim["n"],
                          "busy": sim["skip"]["занята"], "total_usd": round(sim["total_usd"], 2),
                          "fill_mean": round(sim["fill"], 4)}
            print(f"{month} {cell}: сутки готовы {len(days)}, форма {form}, сигналов (кругов) {len(rows)}, "
                  f"взято {sim['n']} (занята {sim['skip']['занята']}), ${sim['total_usd']:+.2f}, заполнение {sim['fill']:.2f}")
        all_days = sorted(set(daily["лимит"]) | set(daily["рынок"]))
        deltas = [daily["рынок"].get(d, 0.0) - daily["лимит"].get(d, 0.0) for d in all_days]
        n = len(deltas)
        total = sum(deltas)
        out = {"cells": info, "n_days_with_money": n, "delta_total": round(total, 2),
               "daily_delta": {d: round(x, 4) for d, x in zip(all_days, deltas)}}
        if n >= 2:
            rng = random.Random(a.seed)
            boot = sorted(sum(deltas[rng.randrange(n)] for _ in range(n)) for _ in range(a.n_boot))
            mean = sum(boot) / len(boot)
            se = math.sqrt(sum((b - mean) ** 2 for b in boot) / (len(boot) - 1))
            z = total / se if se > 0 else 0.0
            p = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))
            ne = en.effective_n_from_autocorr(deltas)
            out.update({"ci95": [round(boot[int(0.025 * len(boot))], 2), round(boot[int(0.975 * len(boot))], 2)],
                        "p_raw": p, "n_eff": None if ne is None else round(ne, 1)})
            print(f"{month}: Δ$ рынок − лимит = {total:+.2f} [{out['ci95'][0]:+.2f}; {out['ci95'][1]:+.2f}], p = {p:.4f}, "
                  f"суток с деньгами {n}, эфф. N {'не определено' if ne is None else f'{ne:.1f}'}")
        else:
            out.update({"ci95": None, "p_raw": None, "n_eff": None})
            print(f"{month}: Δ$ = {total:+.2f}, суток с деньгами {n} — интервал и p не считаются")
        res[month] = out
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
