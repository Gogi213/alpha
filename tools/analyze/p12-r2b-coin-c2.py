#!/usr/bin/env python3
"""TK-120: П-12 §9 п.3 С2 «без монеты» для R2-B (free, cap0). Вход: data/j120/coin-rows-r2b.csv (p12-r2b-coinrows.py на calc: сделки closes -> символ), голова p12_r2b_head.py.
Прошедшая клетка (e133): монета, чьё удаление (из клетки и из B1) сильнее всего снижает ΔSR -> ΔSR без неё и нижняя 95% (блоки общие, как p12lib.analyse).
Опровергнутые (вред п.1): монета, чьё удаление сильнее всего ПОДНИМАЕТ ΔSR -> ΔSR без неё и верхняя 95%; опровержение устойчиво, если верх < 0.
Запуск: P12_POOL_FROM=1 python tools/analyze/p12-r2b-coin-c2.py  ->  docs/findings/p12-coin-c2-free-2026-10-12-r2b.csv"""
import csv, json, os
from collections import defaultdict
import numpy as np
import p12args; p12args.apply()
from p12lib import POOL, block_idx, load, pack, sr

P = pack(load("tools/analyze/p12_r2b_head.py", "free"), False)
res = {r["cell"]: r for r in csv.DictReader(open("docs/findings/p12-sharpe-free-2026-10-12-r2b.csv", encoding="utf-8"))}
ns = load("tools/analyze/p12_r2b_head.py", "free")
ORDER, day_of = [n for n, _ in ns["ORDER"]], ns["day_of"]
months = sorted(POOL)
cal0 = next(iter(P.values()))["cal"]
keep = np.array([d[:7] in POOL for d in cal0]); cal = [d for d, k in zip(cal0, keep) if k]; n = len(cal)
mon = np.array([d[:7] for d in cal]); dpos = {d: i for i, d in enumerate(cal)}
ROWS = defaultdict(list)   # клетка -> [(день, $, символ)]
for m in months:
    names = {f.split("@", 1)[1]: c for f, c in zip(json.load(open(f"data/p12r2b/closes-r2bd-cap0-{m}.json")), ORDER)}
    for r in csv.DictReader(open("data/j120/coin-rows-r2b.csv", encoding="utf-8")):
        if r["month"] != m:
            continue
        c = "B1" if r["form"] == "B1" else names[r["form"]]
        ROWS[c].append((day_of(int(r["exit_ms"])), float(r["usd"]), r["symbol"]))


def daily(rows, drop=None):
    a = np.zeros(n)
    for d, v, s in rows:
        if s != drop and d in dpos:
            a[dpos[d]] += v
    return a


idx = block_idx(np.random.default_rng(66), n)
B1 = ROWS["B1"]
base_dm = P["B1"]["dfn"] & POOL
out = [["cell", "dm", "d_sr_csv", "d_sr_rows", "kind", "coin", "d_sr_without", "ci_lo", "ci_hi", "robust"]]
for c, r in res.items():
    if not (r["passed"] == "1" or r["refuted"] == "1"):
        continue
    rows = ROWS[c]
    dm = P[c]["dfn"] & base_dm
    mk = np.isin(mon, sorted(dm)).astype(float); ms = mk[idx]
    full = float(sr(daily(rows), mk) - sr(daily(B1), mk))
    coins = sorted({x[2] for x in rows} | {x[2] for x in B1})
    ds = {k: float(sr(daily(rows, k), mk) - sr(daily(B1, k), mk)) for k in coins}
    passed = r["passed"] == "1"
    coin = min(ds, key=ds.get) if passed else max(ds, key=ds.get)
    dd = sr(daily(rows, coin)[idx], ms) - sr(daily(B1, coin)[idx], ms)
    lo, hi = float(np.percentile(dd, 2.5)), float(np.percentile(dd, 97.5))
    robust = (ds[coin] >= 0 and lo >= 0) if passed else hi < 0
    out.append([c, len(dm), r["d_sr"], round(full, 4), "pass" if passed else "refuted", coin, round(ds[coin], 4), round(lo, 4), round(hi, 4), int(robust)])
    print(out[-1])
with open("docs/findings/p12-coin-c2-free-2026-10-12-r2b.csv", "w", encoding="utf-8", newline="") as fh:
    csv.writer(fh, lineterminator="\n").writerows(out)
print("устойчиво:", sum(1 for o in out[1:] if o[-1]), "из", len(out) - 1)
