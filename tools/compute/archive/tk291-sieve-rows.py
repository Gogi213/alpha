#!/usr/bin/env python3
"""TK-291: строки сита П-19 для sieve.jsonl (вердикт сита и «просеяно» в Летописи): по клетке — p, сделки и сутки половины А, годных 0.
tk291-sieve-rows.py <deploy> <sieve F1∪F2 csv> <выход>   (порядок add-cells == add-plan_cells)"""
import csv, json, sys
D, src, out = sys.argv[1:4]
rows = {r["vec"]: r for r in csv.DictReader(open(src, encoding="utf-8"))}
cells = [json.loads(l) for l in open(f"{D}/add-cells.jsonl", encoding="utf-8") if l.strip()]
plan = [json.loads(l) for l in open(f"{D}/add-plan_cells.jsonl", encoding="utf-8") if l.strip()]
assert len(cells) == len(plan) == 68
with open(out, "w", encoding="utf-8", newline="\n") as f:
    for c, p in zip(cells, plan):
        r = rows[c["set_name"][4:]]
        f.write(json.dumps({"protocol": "P-19", "fraction": p["fraction"], "hyp": "М-04", "cell_hash": p["cell_hash"], "side": "A",
                            "p": float(r["p"]), "trades": int(r["trades_A"]), "days": int(r["days_A"]), "eligible": 0, "plateau": 0, "edge": 0,
                            "rule_version": "P-19@v3"}, ensure_ascii=False) + "\n")
print("строк сита", len(cells))
