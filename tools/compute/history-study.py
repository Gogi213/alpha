#!/usr/bin/env python3
"""Сколько истории захватывать в силу плотности (владелец 2026-09-18: «в текущую
мс на реальном стакане — не вся правда»).

Проверка на готовых касаниях (`touches-<SYM>.csv`, k = 1.0, любые уровни) без
нового кода: у касания уже есть история уровня — возраст `age_ms` (сколько он
простоял до касания) и устойчивость `size_at_touch / size_max_before` (не
растаял ли к касанию). Вопросы:
  1. При том же числе сетапов даёт ли «сила сейчас + история» больше отскока,
     чем «сила сейчас»? Для каждого T из AGE_GRID: среди касаний с age ≥ T
     берутся top-k по силе (k = число касаний с силой ≥ S0 без истории) —
     сравнение при равной селективности, по монете, затем пул.
  2. Какой T — по пулу и по числу монет, где лифт ≥ 0.
  3. То же для устойчивости (size_at/size_max ≥ STAB).
Использование: history-study.py <dir-with-touches> <instruments.csv>
"""
import csv
import glob
import os
import sys
from collections import defaultdict

S0 = 300.0
AGE_GRID_S = [0, 1, 5, 15, 60, 300, 900, 3600]
STAB = 0.8
NMIN = 30


def load_instruments(path):
    inst = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#") or line.startswith("symbol"):
                continue
            p = line.strip().split(",")
            if len(p) >= 4:
                inst[p[0]] = (float(p[1]), float(p[3]))
    return inst


def read(path, tick, lot):
    out = []
    with open(path, encoding="utf-8") as f:
        r = csv.DictReader(l for l in f if not l.startswith("#"))
        for x in r:
            try:
                s20 = float(x["strength_w20_pct"]) if x["strength_w20_pct"] else None
                if s20 is None:
                    continue
                age = int(x["age_ms"]) / 1000.0
                sat, smax = int(x["size_at_touch"]), int(x["size_max_before"])
                stab = sat / smax if smax > 0 else 0.0
                notional = int(x["price_tick"]) * tick * sat * lot
                bounced = x["ended_by_death"] != "true"
                m60 = float(x["m_60s"]) if x["m_60s"] not in ("", "none") else None
                days = x["day_utc"]
            except (KeyError, ValueError):
                continue
            out.append((s20, age, stab, notional, bounced, m60, days))
    return out


def share(sel):
    return (sum(1 for r in sel if r[4]) / len(sel)) if sel else None


def main():
    d, inst_path = sys.argv[1], sys.argv[2]
    inst = load_instruments(inst_path)
    pool = defaultdict(lambda: [0, 0])       # key -> [n, bounced]
    coins_lift = defaultdict(lambda: [0, 0])  # key -> [coins with lift>=0, coins compared]
    kept = defaultdict(float)
    ncoins = 0
    for fp in sorted(glob.glob(os.path.join(d, "touches-*.csv"))):
        sym = os.path.basename(fp)[8:-4]
        if sym not in inst:
            continue
        rows = read(fp, *inst[sym])
        if not rows:
            continue
        days = len({r[6] for r in rows}) or 1
        rows = [r for r in rows if r[3] >= 1000.0]  # денежный пол $1k как в кресте
        base_sel = [r for r in rows if r[0] >= S0]
        k = len(base_sel)
        if k < NMIN:
            continue
        ncoins += 1
        base_share = share(base_sel)
        pool[("now", 0)][0] += k; pool[("now", 0)][1] += sum(1 for r in base_sel if r[4])
        kept[("now", 0)] += k / days
        # 1. равная селективность: age ≥ T, top-k по силе
        for T in AGE_GRID_S:
            cand = sorted((r for r in rows if r[1] >= T), key=lambda r: -r[0])[:k]
            if len(cand) < NMIN:
                continue
            sh = share(cand)
            key = ("age_topk", T)
            pool[key][0] += len(cand); pool[key][1] += sum(1 for r in cand if r[4])
            kept[key] += len(cand) / days
            coins_lift[key][1] += 1
            if sh >= base_share:
                coins_lift[key][0] += 1
        # 2. простой И: сила ≥ S0 и age ≥ T (селективность падает)
        for T in AGE_GRID_S:
            sel = [r for r in base_sel if r[1] >= T]
            key = ("age_and", T)
            pool[key][0] += len(sel); pool[key][1] += sum(1 for r in sel if r[4])
            kept[key] += len(sel) / days
            if len(sel) >= NMIN:
                coins_lift[key][1] += 1
                if share(sel) >= base_share:
                    coins_lift[key][0] += 1
        # 3. устойчивость: top-k по силе среди stab ≥ STAB; и «И»
        cand = sorted((r for r in rows if r[2] >= STAB), key=lambda r: -r[0])[:k]
        if len(cand) >= NMIN:
            key = ("stab_topk", STAB)
            pool[key][0] += len(cand); pool[key][1] += sum(1 for r in cand if r[4])
            kept[key] += len(cand) / days
            coins_lift[key][1] += 1
            if share(cand) >= base_share:
                coins_lift[key][0] += 1
        sel = [r for r in base_sel if r[2] >= STAB]
        key = ("stab_and", STAB)
        pool[key][0] += len(sel); pool[key][1] += sum(1 for r in sel if r[4])
        kept[key] += len(sel) / days
        if len(sel) >= NMIN:
            coins_lift[key][1] += 1
            if share(sel) >= base_share:
                coins_lift[key][0] += 1

    def row(key):
        p = pool[key]; c = coins_lift[key]
        sh = p[1] / p[0] * 100 if p[0] else float("nan")
        return f"{sh:5.1f}%  kept/day={kept[key]/max(ncoins,1):7.1f}  coins lift>=0: {c[0]:3d}/{c[1]:<3d}"

    print(f"coins={ncoins}  base = сила ≥ {S0:.0f} % сейчас, $1k пол; сравнение при равной селективности (top-k по силе)")
    print(f"  сила сейчас:                {row(('now', 0))}")
    print("== история как возраст уровня (age ≥ T), равная селективность:")
    for T in AGE_GRID_S:
        print(f"  age ≥ {T:>5} s, top-k по силе: {row(('age_topk', T))}")
    print("== сила ≥ 300 % И age ≥ T (селективность падает):")
    for T in AGE_GRID_S:
        print(f"  age ≥ {T:>5} s:               {row(('age_and', T))}")
    print(f"== устойчивость (size_at/size_max ≥ {STAB}):")
    print(f"  top-k по силе среди устойчивых: {row(('stab_topk', STAB))}")
    print(f"  сила ≥ 300 % И устойчивый:      {row(('stab_and', STAB))}")


if __name__ == "__main__":
    main()
