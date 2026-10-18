#!/usr/bin/env python3
"""TK-291 (П-19 §6, Судья P-19-load-check): строка «стопка» — ячейка той же формы, что 68 клеток: id = sha256(JSON)[:16], set p19/stack,
params.lot_by_n = null, статус «не определена» + причина; без results; хеш канонический (cfg_json); причина строки — metrics_why.jsonl.
tk291-load-stack.py <выход каталог> <run_id>  ->  add-{cells,run_cells,plan_cells,metrics_why}.jsonl"""
import hashlib, json, os, sys
sys.path.insert(0, os.environ.get("REGTOOLS", "/opt/alpha-launch/tools/registry"))
import cellhash as ch
import cellhash_reg as cr

out, run = sys.argv[1:3]
BASE = "30229bf7071fb028"
STATUS = "не определена"
REASON = "стопка не определена: плато нет ни на одной оси F1+F2 (68 клеток, мин. p 0,2062); Холм m=5 p=1"
cfg = {"form": cr.B1_FORM, "set": cr.B1_SET, "exec": dict(cr.STD_EXEC), "sigma": cr.B1_SIGMA, "universe": cr.UNIVERSE["v171c"],
       "variant": {"family": "p19", "axes": {"stack": STATUS}}}
cfg_json, h = ch.canonical_json(cfg), ch.cell_hash(cfg)
params = {"group": "p19-psim", "lot_by_n": None, "status": STATUS, "reason": REASON, "base": BASE, "cfg_json": cfg_json}
cell = {"logic_version": "bounce-v1", "form": "B1", "set_name": "p19/stack", "params": params}
cid = hashlib.sha256(json.dumps(cell, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
rows = {"cells": {"id": cid, **cell}, "run_cells": {"run_id": run, "cell_id": cid, "hyp_id": "М-04"},
        "plan_cells": {"protocol": "P-19", "fraction": 2, "hyp": "М-04", "cell_hash": h,
                       "axes": json.dumps({"ось": "стопка", "k": STATUS}, ensure_ascii=False), "cfg": cfg_json},
        "metrics_why": {"hash": h, "why": REASON}}
os.makedirs(out, exist_ok=True)
for b in ("cells", "run_cells", "plan_cells", "metrics_why"):
    with open(os.path.join(out, f"add-{b}.jsonl"), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(rows[b], ensure_ascii=False) + "\n")
open(os.path.join(out, "stack.hash"), "w").write(h + "\n")
print("id", cid, "хеш", h)
