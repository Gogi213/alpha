#!/usr/bin/env python3
"""TK-272: строки сита F2 Г-10 / Г-112 -> docs/registry/sieve.jsonl (P-14, fraction 2, сторона А, как master.sieve).
Клетки F2 — plan_cells (P-14, fraction 2); статистика — sieve.csv (tk278-sieve.py), плато — plateau.csv (tk237-plateau.py, F1 ∪ F2).
  python tk272-sieve-rows.py <sieve.csv> <plateau.csv> <registry-dir> <out-cells.csv>   (повтор безопасен: ключ protocol/fraction/hash/side)"""
import csv, json, sys

sv = {r["hash"]: r for r in csv.DictReader(open(sys.argv[1], encoding="utf-8"))}
pl = {r["hash"]: r["plateau"] for r in csv.DictReader(open(sys.argv[2], encoding="utf-8"))}
reg = sys.argv[3]
HY = ("Г-10", "Г-112")
cells = []
for l in open(f"{reg}/plan_cells.jsonl", encoding="utf-8"):
    d = json.loads(l)
    if d["protocol"] == "P-14" and d["fraction"] == 2 and d["hyp"] in HY:
        cells.append((d["hyp"], d["cell_hash"], d["axes"]))
rows = []
for hyp, h, axes in cells:
    r = sv[h]
    ok = int(r["status"] == "годна")
    lab = pl.get(h, "")
    rows.append({"protocol": "P-14", "fraction": 2, "hyp": hyp, "cell_hash": h, "side": "A", "p": float(r["p"]) if ok else 1.0,
                 "trades": int(float(r["trades_A"])), "days": int(float(r["days_A"])), "eligible": ok,
                 "plateau": int(ok and lab.startswith("плато") and lab != "плато нет"), "edge": int(ok and "у границы" in lab),
                 "rule_version": "P-14@r2"})
fn = f"{reg}/sieve.jsonl"
have = {(d["protocol"], d["fraction"], d["cell_hash"], d["side"]) for d in map(json.loads, filter(str.strip, open(fn, encoding="utf-8")))}
new = [r for r in rows if (r["protocol"], r["fraction"], r["cell_hash"], r["side"]) not in have]
raw = open(fn, "rb").read()
with open(fn, "ab") as f:
    if raw and raw[-1:] != b"\n":
        f.write(b"\n")
    f.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in new).encode("utf-8"))
with open(sys.argv[4], "w", encoding="utf-8", newline="") as f:
    w = csv.writer(f)
    w.writerow(["hyp", "hash", "axes", "trades_A", "days_A", "dSR", "p", "se", "status", "plateau"])
    for hyp, h, axes in cells:
        r = sv[h]
        w.writerow([hyp, h, axes, r["trades_A"], r["days_A"], r["dSR"], r["p"], r["se"], r["status"], pl.get(h, "")])
for hy in HY:
    rr = [r for r in rows if r["hyp"] == hy]
    print(hy, "клеток", len(rr), "годных", sum(r["eligible"] for r in rr), "плато", sum(r["plateau"] for r in rr), "у края", sum(r["edge"] for r in rr))
print("строк в sieve.jsonl +", len(new), "из", len(rows))
