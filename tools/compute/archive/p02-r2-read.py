#!/usr/bin/env python3
"""П-02: перечтение готовых результатов по правилу R2 (§12; владелец 26.09, принято Судьёй `b39d9c5`) — без нового счёта.

По месяцам отдельно (август 01–31, сентябрь 01–23; В-115):
  1. точечный эффект — та же статистика, что в оценке: блок A — объединённая разность долей `bounced`
     (счётчики суммируются по суткам, потом доли), блок B — сумма суточных Δ$ (клетка − база, календарные сутки);
  2. движущийся круговой блочный бутстреп по суткам, b = ⌈n^{1/3}⌉ (31 → 4, 23 → 3), 20 000 повторов, seed 20260926;
     p = 2·min(доля ≤ 0, доля ≥ 0); справочно b = 1 и 2b;
  3. устойчивость: влияние суток = эффект − эффект без суток; снять сутки с наибольшим влиянием в сторону эффекта,
     затем два таких дня разом — знак сохраняется? (монеты — только для прошедших 1, 2, 4 в обоих месяцах, здесь
     не считаются — печатается «нужна проверка по монетам»);
  4. Холм по пакету в каждом месяце: m = 11 (6 блока A, H7, H8 = 1, H9 = 1, H10, пара = 1).
Знаковый тест по суткам с поправкой дисперсии n / n_eff — справочно. «+» везде — в сторону гипотезы.

    python tools/compute/p02-r2-read.py --out docs/findings/p02-r2-read-2026-09-26.json
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "..", "data", "p02")
MONTHS = {"август": ("2026-08", 31), "сентябрь": ("2026-09", 23)}
# гипотеза → (файл счётов, вариант, ключ «в сторону гипотезы», ключ базы)
BLOCK_A = {
    "Г-28 (H1)": ("p02-wall-counts-2026-09-26.json", "g28_main", "top", "bottom"),
    "Г-33 (H2)": ("p02-g33-counts-2026-09-26.json", "g33_chain2", "no", "yes"),
    "Г-07 (H3)": ("p02-wall2-counts-2026-09-26.json", "g07_main", "top", "bottom"),
    "Г-08 (H4)": ("p02-wall-counts-2026-09-26.json", "g08_main", "high", "low"),
    "Г-46 (H5)": ("p02-wave3-counts-2026-09-26.json", "g46_mismatch60", "mismatch", "match"),
    "Г-36 (H6)": ("p02-g36-counts-2026-09-26.json", "g36_iceberg", "yes", "no"),
}
BLOCK_B = {"Г-85 (H7)": "p02b-H7.json", "Г-105 (H10)": "p02b-H10.json"}
FIXED_P1 = ["Г-88 (H8)", "Г-86 (H9)", "пара Г-85×Г-105"]


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, fname))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


EN = _load("h10fix", "p02-h10-fix-analyze.py")  # effective_n с потолком n


def stat_a(units):
    """units: список (t_b, t_n, b_b, b_n) по суткам → разность долей объединённо, п.п."""
    tb = sum(u[0] for u in units); tn = sum(u[1] for u in units)
    bb = sum(u[2] for u in units); bn = sum(u[3] for u in units)
    if tn == 0 or bn == 0:
        return None
    return (tb / tn - bb / bn) * 100.0


def stat_b(units):
    return sum(units)


def block_boot(units, stat, b, reps, seed):
    n = len(units)
    rng = random.Random(seed)
    k = math.ceil(n / b)
    out = []
    for _ in range(reps):
        sample = []
        for _ in range(k):
            s = rng.randrange(n)
            sample.extend(units[(s + j) % n] for j in range(b))
        v = stat(sample[:n])
        if v is not None:
            out.append(v)
    out.sort()
    lo, hi = out[int(0.025 * len(out))], out[int(0.975 * len(out)) - 1]
    p = 2 * min(sum(v <= 0 for v in out), sum(v >= 0 for v in out)) / len(out)
    return lo, hi, min(1.0, p)


def sign_test(day_effects):
    s = [1.0 if x > 0 else -1.0 for x in day_effects if x != 0]
    n = len(s)
    if n < 2:
        return None
    k = sum(x > 0 for x in s)
    ne = EN.effective_n_from_autocorr(s)
    var = n / 4 * (n / ne)
    z = (k - n / 2) / math.sqrt(var)
    return {"days_plus": k, "days": n, "p": 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))}


def read(units, days, stat, day_effect, reps, seed):
    n = len(units)
    b = math.ceil(n ** (1 / 3))
    est = stat(units)
    lo, hi, p = block_boot(units, stat, b, reps, seed)
    ref = {bb: block_boot(units, stat, bb, reps // 4, seed)[2] for bb in (1, 2 * b)}
    infl = []
    for i in range(n):
        rest = stat(units[:i] + units[i + 1:])
        infl.append((est - rest) if rest is not None else 0.0)
    sgn = 1 if est > 0 else -1
    order = sorted(range(n), key=lambda i: -sgn * infl[i])
    drop1 = stat([u for j, u in enumerate(units) if j != order[0]])
    drop2 = stat([u for j, u in enumerate(units) if j not in order[:2]])
    return {"n_days": n, "b": b, "est": est, "ci95": [lo, hi], "p_boot": p, "p_ref_b1_2b": ref,
            "drop_top1": {"day": days[order[0]], "est": drop1}, "drop_top2": {"days": [days[i] for i in order[:2]], "est": drop2},
            "sign_kept_after_drops": all(v is not None and v * est > 0 for v in (drop1, drop2)),
            "sign_test": sign_test([day_effect(u) for u in units]),
            "n_eff": EN.effective_n_from_autocorr([day_effect(u) for u in units])}


def holm(ps):
    m = len(ps)
    order = sorted(range(m), key=lambda i: ps[i])
    adj = [0.0] * m
    run = 0.0
    for r, i in enumerate(order):
        run = max(run, min(1.0, (m - r) * ps[i]))
        adj[i] = run
    return adj


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reps", type=int, default=20000)
    ap.add_argument("--seed", type=int, default=20260926)
    ap.add_argument("--out")
    a = ap.parse_args()
    res = {}
    for h, (f, var, up, base) in BLOCK_A.items():
        dc = json.load(open(os.path.join(DATA, f), encoding="utf-8"))["day_counts"]
        res[h] = {}
        for mon, (pref, _) in MONTHS.items():
            days = sorted(d for d in dc if d.startswith(pref) and var in dc[d].get("variants", dc[d]))
            units, keep = [], []
            for d in days:
                v = dc[d].get("variants", dc[d])[var]
                u = (v[up][0], v[up][1], v[base][0], v[base][1])
                if u[1] > 0 or u[3] > 0:
                    units.append(u); keep.append(d)
            eff = lambda u: ((u[0] / u[1] if u[1] else 0) - (u[2] / u[3] if u[3] else 0)) * 100 if u[1] and u[3] else 0.0
            res[h][mon] = read(units, keep, stat_a, eff, a.reps, a.seed)
    bdir = os.path.join(DATA, "blockb")
    base = {g["epoch"]: g["daily"] for g in json.load(open(os.path.join(bdir, "p02b-base.json"), encoding="utf-8"))["grid"]}
    for h, f in BLOCK_B.items():
        cell = {g["epoch"]: g["daily"] for g in json.load(open(os.path.join(bdir, f), encoding="utf-8"))["grid"]}
        res[h] = {}
        for mon, (pref, nd) in MONTHS.items():
            days = [f"{pref}-{i:02d}" for i in range(1, nd + 1)]
            units = [cell.get(mon, {}).get(d, 0.0) - base.get(mon, {}).get(d, 0.0) for d in days]
            res[h][mon] = read(units, days, stat_b, lambda x: x, a.reps, a.seed)
            res[h][mon]["base_total"] = sum(base.get(mon, {}).get(d, 0.0) for d in days)
    names = list(BLOCK_A) + list(BLOCK_B) + FIXED_P1
    for mon in MONTHS:
        ps = [res[h][mon]["p_boot"] for h in list(BLOCK_A) + list(BLOCK_B)] + [1.0] * len(FIXED_P1)
        for h, p in zip(names, holm(ps)):
            res.setdefault(h, {}).setdefault(mon, {})["p_holm"] = p
    for h in names:
        r = res[h]
        sig = [r[m].get("est") for m in MONTHS]
        ph = max(r[m]["p_holm"] for m in MONTHS)
        if all(s is not None and s > 0 for s in sig) and ph < 0.05:
            v = "кандидат «подтверждено»: " + ("нужна проверка по монетам" if all(r[m]["sign_kept_after_drops"] for m in MONTHS) else "не держится без 1–2 суток → не ясно")
        elif all(s is not None and s < 0 for s in sig) and ph < 0.05:
            v = "кандидат «опровергнуто»: " + ("нужна проверка по монетам" if all(r[m]["sign_kept_after_drops"] for m in MONTHS) else "не держится без 1–2 суток → не ясно")
        else:
            v = "не ясно"
        r["r2"] = v
        line = " | ".join(
            f"{m}: {r[m]['est']:+.2f} [{r[m]['ci95'][0]:+.2f}; {r[m]['ci95'][1]:+.2f}] p {r[m]['p_boot']:.4f} Холм {r[m]['p_holm']:.4f}"
            f" без 1–2 суток {'держится' if r[m]['sign_kept_after_drops'] else 'НЕТ'}" if r[m].get("est") is not None else f"{m}: p = 1 (не определена)"
            for m in MONTHS)
        print(f"{h}: {line} → {v}")
    if a.out:
        json.dump(res, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
