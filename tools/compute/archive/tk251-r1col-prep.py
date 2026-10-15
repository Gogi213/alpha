#!/usr/bin/env python3
"""TK-251 П3: состав R1COL «к счёту» = R1COL − уже в Летописи (results) − nodata (R1_NODATA); делится на колонки (.r1spec для r1cell.py apply) и композиты
(common_q: Г-64/73/83 — g64-drift, g73-conj, g83-conj; в r1cell.py их нет, берутся из r1ext фев–сен и tk064-r1cells --jan-self для января).
  tk251-r1col-prep.py <каталог вывода>  ->  r1col.r1spec, r1col-comp.csv (hash,hyp,q,name), r1col-hashes.txt"""
import csv, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(HERE, "..", "registry"))
import cellhash as ch, db, master
out = sys.argv[1]; os.makedirs(out, exist_ok=True)
c = master.connect(); grids, _ = master.build_grid(c, "P-14", 1, None, master.rules_of(c, "P-14")); c.close()
have = {d["cell_id"] for d in db.jl("results.jsonl")}
todo = {h: f for h, f in master.R1COL.items() if h not in have and f not in master.R1_NODATA}
FAMNAME = {"Г-64": "g64-drift", "Г-73": "g73-conj", "Г-83": "g83-conj"}
comp, col = [], set()
for hyp, cells in sorted(grids.items()):
    for av, cfg in cells:
        h = ch.cell_hash(cfg)
        if h not in todo:
            continue
        if "common_q" in av:
            comp.append([h, hyp, av["common_q"], f"{FAMNAME[hyp]}-k{av['common_q']}"])
        else:
            col.add(h)
spec = out + "/r1col.r1spec"
os.system(f'python -I -X utf8 "{HERE}/r1cell.py" spec --plan "{HERE}/../../docs/registry/plan_cells.jsonl" --out "{out}/all.r1spec" --which all')
rows = list(csv.DictReader(open(out + "/all.r1spec", encoding="utf-8")))
keep = [r for r in rows if r["hash"] in col]
assert {r["hash"] for r in keep} == col, (len(keep), len(col))
with open(spec, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(keep)
with open(out + "/r1col-comp.csv", "w", newline="", encoding="utf-8") as f:
    csv.writer(f).writerows([["hash", "hyp", "q", "name"]] + comp)
open(out + "/r1col-hashes.txt", "w").write("\n".join(sorted(todo)) + "\n")
os.remove(out + "/all.r1spec")
print("R1COL к счёту", len(todo), "колонки", len(keep), "композиты", len(comp), file=sys.stderr)
