#!/usr/bin/env python3
"""TK-268 ш.3: Г-147 (signal=touch, хеш из master.build_grid P-14 F1) -> g147.cells / g147.args для отдельного захода `--signal touch`.
Клетка = база F1 «форма набор» (та же форма/набор/исполнение), режим входа — флаг прогона, в --cells не кодируется.
  python tools/registry/../compute/tk268-g147-plan.py <out-stem>"""
import json, os, re, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "registry"))
import cellhash as ch, cellhash_reg as cr, master
stem = sys.argv[1]
c = master.connect()
grids, _ = master.build_grid(c, "P-14", 1, ["Г-147"], master.rules_of(c, "P-14"))
c.close()
cells = [cfg for hyp, cl in grids.items() for av, cfg in cl]
assert len(cells) == 1, len(cells)
cfg = cells[0]
sets = cr.load_sets()
name = [n for n, s in sets.items() if cr.set_of(s) == cfg["set"]]
assert len(name) >= 1, "набор не найден"
n = sorted(name)[0]
form = cfg["form"]
m = re.match(r"^(.+?)-(.+?)-(.+?)-(\d+)-ttl(\d+)$", form)
assert m, form
entry, stop, take, dl, ttl = m.groups()
open(stem + ".cells", "w", encoding="utf-8", newline="\n").write(f"{form} {n}\n")
with open(stem + ".args", "w", encoding="utf-8", newline="\n") as f:
    f.write(f"--entry-form {entry}\n--entry-ttl-secs {ttl}\n--stop-form {stop}\n--take-form {take}\n--deadline-secs {dl}\n--set {n}:{sets[n]}\n")
print("hash", ch.cell_hash(cfg), "| line:", form, n, "| sets-кандидатов", name)
