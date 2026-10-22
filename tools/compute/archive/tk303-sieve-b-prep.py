#!/usr/bin/env python3
"""TK-303: вход сита для Г-102 B (32 клетки) — km.json (хеш -> {hyp, keys{мес: 'набор@форма'}}) и cov.csv (hyp,hash,axes,fraction,in_letopis).
Хеш и (k,T) — строки плана (tk303-g102b-plan.py), строка клетки — .cells по (k,T), как load_g102b.py.
  python tools/compute/archive/tk303-sieve-b-prep.py <plan.jsonl> <plan.cells> <km.json> <cov.csv>"""
import csv, json, os, re, sys
sys.path.insert(0, os.path.join("tools", "registry"))
import cellhash as ch
plan_f, cells_f, km_f, cov_f = sys.argv[1:5]
lines = {}
for ln in open(cells_f, encoding="utf-8").read().splitlines():
    m = re.match(r"retreat(\d+)t(\d+)-", ln)
    if m: lines[(int(m[1]), int(m[2]))] = ln
km, rows = {}, []
for l in open(plan_f, encoding="utf-8"):
    if not l.strip(): continue
    d = json.loads(l); h = d.pop("hash"); assert ch.cell_hash(d) == h, h
    e = d["entry"]; ln = lines[(e["k"], e["t"])]; form, _, st = ln.partition(" ")
    km[h] = {"hyp": d["hyp"], "keys": {f"2026-{m:02d}": f"{st}@{form}" for m in range(1, 10)}}
    rows.append([d["hyp"], h, json.dumps({"k": str(e["k"]), "half_min": str(e["t"])}), 2, 1])
json.dump(km, open(km_f, "w", encoding="utf-8"), ensure_ascii=False)
with open(cov_f, "w", encoding="utf-8", newline="") as f:
    w = csv.writer(f); w.writerow(["hyp", "hash", "axes", "fraction", "in_letopis"]); w.writerows(rows)
print("B клеток", len(rows))
