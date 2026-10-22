#!/usr/bin/env python3
"""TK-289: F3 П-18 (6а 8, 6в 72, ось 5 ttl 8, стопка 2 = 90 клеток) -> plan_cells Летописи (fraction 3), чтобы панель показывала строки F3.
Вход — план сита F3 (tk289-sieve-f3-plan-2026-10-21.jsonl: protocol, fraction, hyp, cell_hash, axes); cfg считается как plan_p18 (cfg_of -> normalize), хеш сверяется.
Идемпотентно (заменяет свои строки).  python tk289-plan-f3.py <plan-f3.jsonl> [--dry]"""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "registry")); sys.path.insert(0, HERE)
import cellhash as ch, db, p18_scope as sc

KEY = {"5": "ttl", "6a": "usd_min", "6v": "flow+usd_min", "stack": "stack"}
rows = []
for l in open(sys.argv[1], encoding="utf-8"):
    r = json.loads(l); a = json.loads(r["axes"]); ax = a["ax"]; cfg = ch.normalize(sc.cfg_of(ax, a[KEY[ax]]))
    assert ch.cell_hash(cfg) == r["cell_hash"], ("хеш не совпал", r["axes"])
    rows.append({"protocol": "P-18", "fraction": 3, "hyp": r["hyp"], "cell_hash": r["cell_hash"], "axes": r["axes"], "cfg": json.dumps(cfg, sort_keys=True, ensure_ascii=False)})
print("F3 plan_cells", len(rows), "хешей", len({r["cell_hash"] for r in rows}))
if "--dry" not in sys.argv:
    keys = {(d["protocol"], d["fraction"], d["hyp"], d["cell_hash"]) for d in rows}
    old = [d for d in db.jl("plan_cells.jsonl") if (d["protocol"], d["fraction"], d["hyp"], d["cell_hash"]) not in keys]
    with open(os.path.join(db.REGD, "plan_cells.jsonl"), "w", encoding="utf-8", newline="\n") as f:
        for d in old + rows: f.write(json.dumps(d, ensure_ascii=False) + "\n")
