#!/usr/bin/env python3
"""TK-345 §11: П-15 Г-134 (R1, 18 клеток × янв–сен) — n клетки против квантили порога.
Читает /data/tk0274/g134-r1/{summary.json,thresholds.csv,cells/<мес>/<хеш>.csv}: (а) строк клетки = n_kept сводки;
(б) n_hist порога = n_b1 месяца; (в) |n_kept − keep_q/100·n_hist| ≤ 2 % n_hist + 2 — иначе печатает Q_OFF
(у дискретной колонки порог лежит на ничьей, доля больше номинала)."""
import csv, json
R = "/data/tk0274/g134-r1"
s = json.load(open(R + "/summary.json"))["months"]
th = {(r["month"], r["hash"]): r for r in csv.DictReader(open(R + "/thresholds.csv"))}
spec = {r["hash"]: r["col"] for r in csv.DictReader(open(R + "/g134.r1spec"))}
bad_rows = bad_nh = tot = 0
off = {}
for mo, m in s.items():
    for h, nk in m["n_kept"].items():
        t = th[(mo, h)]
        tot += 1
        rows = sum(1 for _ in open(f"{R}/cells/{mo}/{h}.csv")) - 1
        bad_rows += rows != nk
        bad_nh += int(t["n_hist"]) != m["n_b1"]
        exp = int(t["keep_q"]) / 100 * int(t["n_hist"])
        if abs(nk - exp) > 0.02 * int(t["n_hist"]) + 2:
            off.setdefault(spec[h], []).append((mo, h, int(t["keep_q"]), nk, round(exp, 1)))
print("cells", tot, "rows!=n_kept", bad_rows, "n_hist!=n_b1", bad_nh)
for col, v in off.items():
    print("Q_OFF col", col, len(v), "of", sum(1 for h in spec.values() if h == col) * len(s))
