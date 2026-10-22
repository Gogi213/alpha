#!/usr/bin/env python3
"""TK-237: хеш клетки -> {месяц: ключ варианта 'набор@форма'} для сита (по master.LINES / MONTHLY, как load_f1.py).
  python tools/compute/archive/tk237-keymap.py <out.json>"""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "registry"))
import cellhash as ch, master
c = master.connect(); rules = master.rules_of(c, "P-14")
grids, _ = master.build_grid(c, "P-14", 1, None, rules); c.close()
out = {}
for hyp, cells in grids.items():
    for av, cfg in cells:
        h = ch.cell_hash(cfg)
        if h in out: continue
        if h not in master.MONTHLY and h not in master.LINES: continue   # не посчитана
        mm = {}
        for m in range(1, 10):
            mo = f"2026-{m:02d}"
            ln = master.MONTHLY[h].get(mo) if h in master.MONTHLY else master.LINES[h][0]
            if ln is None: continue
            form, _, st = ln.partition(" ")
            mm[mo] = f"{st}@{form}"
        out[h] = {"hyp": hyp, "keys": mm}
json.dump(out, open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False)
print(len(out))
