#!/usr/bin/env python3
"""TK-272: сито F2 — ключи вариантов (хеш -> {месяц: 'набор@форма'}) и охват сетки F1 ∪ F2 для 7 полных гипотез.
Как tk237-keymap.py, но по build_grid k=1 и k=2; хеш без строки в Летописи в keymap не входит (сито: «нет данных»).
  python tk272-sieve-prep.py <km.json> <coverage.csv> [Г-65,...]"""
import csv, json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "registry"))
import cellhash as ch, master
HY = (sys.argv[3] if len(sys.argv) > 3 else "Г-65,Г-119,Г-114,Г-104,Г-110,Г-129,Г-133").split(",")
c = master.connect(); rules = master.rules_of(c, "P-14")
km, cov = {}, {}
for k in (1, 2):
    grids, _ = master.build_grid(c, "P-14", k, HY, rules)
    for hyp, cells in grids.items():
        for av, cfg in cells:
            h = ch.cell_hash(cfg)
            if h in cov: continue
            cov[h] = (hyp, av, k)
            if h not in master.MONTHLY and h not in master.LINES: continue
            mm = {}
            for m in range(1, 10):
                mo = f"2026-{m:02d}"
                ln = master.MONTHLY[h].get(mo) if h in master.MONTHLY else master.LINES[h][0]
                if ln is None: continue
                form, _, st = ln.partition(" ")
                mm[mo] = f"{st}@{form}"
            km[h] = {"hyp": hyp, "keys": mm}
c.close()
json.dump(km, open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False)
with open(sys.argv[2], "w", encoding="utf-8", newline="") as f:
    w = csv.writer(f); w.writerow(["hyp", "hash", "axes", "fraction", "in_letopis"])
    for h, (hyp, av, k) in cov.items(): w.writerow([hyp, h, json.dumps(av, ensure_ascii=False), k, int(h in km)])
print("клеток", len(cov), "с ключами", len(km), {hy: sum(1 for v in cov.values() if v[0] == hy) for hy in HY})
