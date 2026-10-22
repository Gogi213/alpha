#!/usr/bin/env python3
"""TK-291: строки сетки П-19 для панели (plan_cells.jsonl: protocol P-19, fraction 1 = F1, 2 = F2, hyp М-04) по ячейкам p19/* Летописи.
tk291-plan.py <registry> <выход add-plan_cells.jsonl>; хеш — канонический (cellhash_reg.build), cfg — params.cfg_json."""
import json, os, sys
sys.path.insert(0, os.environ.get("REGTOOLS", "/opt/alpha-launch/tools/registry"))
import cellhash_reg as cr
reg, out = sys.argv[1:3]
cr.REG = reg
h = {r[0]: r[1] for r in cr.build()}
frac = {}
for l in open(os.path.join(reg, "results.jsonl"), encoding="utf-8"):
    if l.strip() and '"p19-psim"' in l:
        d = json.loads(l); frac[d["cell_id"]] = 1 if d["ext"]["fraction"] == "F1" else 2
AX = ["A · n=0 (первое касание)", "B · n=1", "C · n=2", "D · n≥3"]
n = 0
with open(out, "w", encoding="utf-8", newline="\n") as f:
    for l in open(os.path.join(reg, "cells.jsonl"), encoding="utf-8"):
        if not l.strip() or '"p19/' not in l:
            continue
        c = json.loads(l)
        if not c["set_name"].startswith("p19/"):
            continue
        k = c["params"]["lot_by_n"]
        a = [i for i, x in enumerate(k) if x != 1.0]
        assert len(a) == 1, c["set_name"]
        f.write(json.dumps({"protocol": "P-19", "fraction": frac[c["id"]], "hyp": "М-04", "cell_hash": h[c["id"]],
                            "axes": json.dumps({"ось": AX[a[0]], "k": k[a[0]]}, ensure_ascii=False), "cfg": c["params"]["cfg_json"]}, ensure_ascii=False) + "\n")
        n += 1
print("строк плана", n)
