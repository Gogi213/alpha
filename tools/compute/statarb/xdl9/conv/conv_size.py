#!/usr/bin/env python3
"""TK-493, П-38 r2 G-C2-8: тест размера интервалов допуска A2 для n = 4, 6, 7, 11, 13 суток и сценария «победитель из 180 комбинаций» на синтетическом нуле.

Правило A2 (§7.5): нижние границы ОБОИХ 90 %-интервалов (блочный t `portfolio.block_t_interval` и студентизованный блочный бутстреп-t, B = 3000, seed 37) > 0.
Длина блока - как в боевом коде: ⌈n^{1/3}⌉ (`portfolio.block_len`); n_эфф = ⌈n/блок⌉.
Часть 1 (single): на сценариях нуля (normal, t3, sparse, ar1 - как в size_test.py) доля «нижняя граница > 0» у t, бутстрепа-t и у обоих (A2).
Часть 2 (winner): из K независимых нулевых рядов выбирается ряд с наибольшей нижней границей блочного t (правило §7.4: максимум LCB), для победителя проверяется A2 целиком.
  K = 180 (все комбинации независимы - верхняя оценка ложного прохождения при отборе) и K = 12 (ранг комбинаций не выше 12 - нижняя оценка); истинная зависимость комбинаций неизвестна,
  поэтому печатаются обе границы.
Использует функции size_test.py (векторный бутстреп-t, генераторы нуля); числа - только из расчёта.
  python conv_size.py --reps 2000 --boot 3000 --seed 37 --winner-reps 1000 --out DIR
"""
import argparse
import csv
import importlib.util
import json
import math
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))


def _find_statarb():
    for base in (os.environ.get("C2_STATARB"), os.path.join(HERE, "..", ".."), os.path.expanduser("~/xdl9/tools/statarb")):
        if base and os.path.isfile(os.path.join(base, "size_test.py")):
            return base
    raise SystemExit("не найден size_test.py (C2_STATARB)")


BASE = _find_statarb()
spec = importlib.util.spec_from_file_location("c2_size_test", os.path.join(BASE, "size_test.py"))
ST = importlib.util.module_from_spec(spec)
sys.modules["c2_size_test"] = ST
spec.loader.exec_module(ST)
P = ST.P

SIZES = (4, 6, 7, 11, 13)
ALPHA = 0.10


def lower_bounds(x, n_boot, rb):
    """-> (нижняя блочного t, нижняя бутстрепа-t) для ряда x с блоком ⌈n^{1/3}⌉."""
    b = P.block_len(len(x))
    (_, bt) = ST.bounds_bootstrap(x, n_boot, rb, ALPHA, b)
    tb = ST.bounds_t_block(x, ALPHA, b)
    return tb[0], bt[0]


def single(scen, n, reps, n_boot, seed):
    rng = np.random.default_rng(seed)
    xs = ST.gen(rng, scen, reps, n)
    rb = np.random.default_rng(seed + 1)
    lt, lb = [], []
    for x in xs:
        a, b = lower_bounds(x, n_boot, rb)
        lt.append(a)
        lb.append(b)
    lt, lb = np.array(lt), np.array(lb)
    return dict(scen=scen, n=n, block=P.block_len(n), n_eff=math.ceil(n / P.block_len(n)), reps=reps, p_t=float((lt > 0).mean()), p_boot_t=float((lb > 0).mean()),
                p_a2=float(((lt > 0) & (lb > 0)).mean()), se=math.sqrt(0.05 * 0.95 / reps))


def winner(scen, n, K, reps, n_boot, seed):
    rng = np.random.default_rng(seed + 100 * K + n)
    rb = np.random.default_rng(seed + 7)
    hit_t = hit_a2 = 0
    for _ in range(reps):
        xs = ST.gen(rng, scen, K, n)
        b = P.block_len(n)
        lo = np.array([P.block_t_interval(list(map(float, x)), ALPHA, b)[1] for x in xs])
        w = int(np.argmax(lo))
        if lo[w] > 0:
            hit_t += 1
            _, lb = lower_bounds(xs[w], n_boot, rb)
            if lb > 0:
                hit_a2 += 1
    return dict(scen=scen, n=n, K=K, reps=reps, p_winner_t_lower_gt0=hit_t / reps, p_winner_a2=hit_a2 / reps, se=math.sqrt(0.05 * 0.95 / reps))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reps", type=int, required=True)
    ap.add_argument("--boot", type=int, required=True)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--winner-reps", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--parts", default="single,winner")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    t0 = time.time()
    res = {"single": [], "winner": []}
    if "single" in a.parts:
        for scen in ST.SCENARIOS:
            for n in SIZES:
                r = single(scen, n, a.reps, a.boot, a.seed)
                res["single"].append(r)
                print("single %-6s n=%2d блок %d n_эфф %d: t=%.3f бутстреп-t=%.3f A2=%.3f (se %.3f)" % (scen, n, r["block"], r["n_eff"], r["p_t"], r["p_boot_t"], r["p_a2"], r["se"]), flush=True)
    if "winner" in a.parts:
        for scen in ("normal", "t3", "sparse"):
            for n in (6, 7, 13):
                for K in (12, 180):
                    r = winner(scen, n, K, a.winner_reps, a.boot, a.seed)
                    res["winner"].append(r)
                    print("winner %-6s n=%2d K=%3d: нижняя t победителя >0: %.3f; A2 победителя: %.3f (se %.3f)" % (scen, n, K, r["p_winner_t_lower_gt0"], r["p_winner_a2"], r["se"]), flush=True)
    res["seconds"] = round(time.time() - t0, 1)
    json.dump(res, open(os.path.join(a.out, "g_c2_8.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, sort_keys=True)
    for part, rows in (("single", res["single"]), ("winner", res["winner"])):
        if rows:
            with open(os.path.join(a.out, "g_c2_8_%s.csv" % part), "w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), lineterminator=chr(10))
                w.writeheader()
                for r in rows:
                    w.writerow(r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
