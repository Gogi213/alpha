#!/usr/bin/env python3
"""TK-190: замена авг/сен R2-B в results.jsonl (run R-20261012-001) числами closes-r2bf (psim с 3 досчитанными символо-сутками).
  python tools/registry/patch_r2bf.py <closes-r2bf-sum.csv> [--dry]"""
import csv, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db
RUN = "R-20261012-001"
agg = {}
for r in csv.DictReader(open(sys.argv[1], encoding="utf-8")):
    agg.setdefault((r["variant"], r["month"]), {})[r["cap"]] = (int(r["n"]), float(r["sum_usd"]))
res = db.jl("results.jsonl"); n = ch = 0
for d in res:
    if d["run_id"] != RUN or d["month"] not in ("2026-08", "2026-09"):
        continue
    form, _, st = d["ext"]["cell_line"].partition(" ")
    caps = agg[(f"{st}@{form}", d["month"])]; n += 1
    n0, s0 = caps["0"]; new3 = caps["3"]
    if (d["trades"], d["pnl_usd"], d["ext"].get("n_cap3"), d["ext"].get("pnl_usd_cap3")) != (n0, s0, new3[0], new3[1]):
        ch += 1
    d["trades"], d["pnl_usd"] = n0, s0; d["ext"]["n_cap3"], d["ext"]["pnl_usd_cap3"] = new3
print("строк авг/сен", n, "изменено", ch)
if "--dry" not in sys.argv:
    db._wjl("results.jsonl", res)
