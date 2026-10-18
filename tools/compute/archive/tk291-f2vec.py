#!/usr/bin/env python3
"""TK-291 (П-19 §4, П-14 §11.2): векторы F2 по сито-CSV половины А (tk291-sieve.py) — правило по порядку, без выбора руками.
tk291-f2vec.py <sieve-A.csv> <выход: строки метка=k0,k1,k2,k3>
Ось: зона = лучшая определённая клетка (годные — p ↑; если годных нет, одна: p ↑, dSR ↓, суток ↓; k=0 — отсечка, не в выборе).
Окрестность = зона + по одному соседу значения-сетки с каждой стороны (k=1 — значение сетки, сосед допустим); шаг F2 = (сосед − зона)/4,
3 новые точки на сторону (≤ 12/ось). Годные зоны несвязные — до 3 по минимальному p (П-14 §11.2 п.2); без годных — одна зона."""
import csv
import sys

GRID = [0.25, 0.375, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0]
rows = {r["vec"]: r for r in csv.DictReader(open(sys.argv[1], encoding="utf-8"))}
out = []
for ax in range(4):
    cells = [(float(l[3:]), r) for l, r in ((f"a{ax}k{k:g}", rows.get(f"a{ax}k{k:g}")) for k in GRID if k != 1.0) if r]
    cells = [(k, r) for k, r in cells if int(r["trades_A"]) >= 20 and int(r["days_A"]) >= 10]
    good = [(k, r) for k, r in cells if r["status"] == "годна"]
    key = lambda kr: (float(kr[1]["p"]), -float(kr[1]["dSR"]), -int(kr[1]["days_A"]))
    zones = sorted(good, key=key)[:3] if good else sorted(cells, key=key)[:1]
    for k, _ in zones:
        i = GRID.index(k)
        for j in (i - 1, i + 1):
            if 0 <= j < len(GRID):
                for t in (1, 2, 3):
                    v = k + (GRID[j] - k) * t / 4
                    vec = [1.0] * 4; vec[ax] = v
                    out.append((f"a{ax}k{v:g}", vec))
seen = {}
for lab, vec in out:
    seen[lab] = vec
with open(sys.argv[2], "w", encoding="utf-8", newline="\n") as f:
    for lab, vec in seen.items():
        f.write(f"{lab}=" + ",".join(f"{x:g}" for x in vec) + "\n")
print(len(seen), "векторов F2 →", sys.argv[2])
