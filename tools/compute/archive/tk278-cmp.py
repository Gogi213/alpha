#!/usr/bin/env python3
"""TK-278: сверка closes с монетой с готовыми closes Летописи (В-213: тот же счёт, добавлена колонка монеты).

  tk278-cmp.py <набор> [мес…]   набор r1x|r1col|r1old|e|k; печатает по месяцу: ключей, равных (closes-out == готовое и срез sym без монеты == готовое), расхождений.
Выход 0 — все равны. Готовое: R1 — closes-cap0-<мес>.json дерева набора; Э — closes-p14f1-cap0-2026-<мм>; К — closes-g117d-cap0-2026-<мм>.
"""
import json
import sys

W = "/data/tk0278/psim"
M = "jan feb mar apr may jun jul aug sep".split()


def orig(s, mm):
    m = M[int(mm) - 1]
    return {"r1x": f"/data/tk0251/ps/closes-cap0-{m}.json", "r1col": f"/data/tk0251/p3/ps/closes-cap0-{m}.json",
            "r1old": f"/data/tk063r1/closes-cap0-{m}.json", "e": f"/data/tk0231/psim/closes-p14f1-cap0-2026-{mm}.json",
            "k": f"/data/tk0266/psim/closes-g117d-cap0-2026-{mm}.json"}[s]


def cut(v):
    """{эпоха: {cap: [[мс, $, монета]…]}} -> то же без монеты."""
    return {e: {k: [r[:2] for r in rows] for k, rows in d.items()} for e, d in v.items()}


def main():
    s, mons = sys.argv[1], sys.argv[2:] or [f"{i:02d}" for i in range(1, 10)]
    bad = 0
    for mm in mons:
        o = json.load(open(orig(s, mm)))
        c = json.load(open(f"{W}/chk-{s}-{mm}.json"))
        y = json.load(open(f"{W}/closes-sym-{s}-cap0-{mm}.json"))
        eq = sum(1 for k, v in o.items() if c.get(k) == v and cut(y.get(k, {})) == v)
        extra = len(set(y) - set(o))
        bad += len(o) - eq
        print(f"{s} {mm}: ключей {len(o)} равны {eq} расходятся {len(o) - eq} лишних в sym {extra}")
    print("ИТОГО расхождений", bad)
    sys.exit(1 if bad else 0)


main()
