"""П-42 §13.8: файл вариантов шага B на 544 монетах → <ROOT>/variants-t544.txt.

  P42_ROOT=data/p42v2-544 python tools/p42/tickvars.py     (топ-3 → top3.json; его variants-tick.txt здесь не используется)
  P42_ROOT=data/p42v2-544 python tools/p42/tick544vars.py   → variants-t544.txt
Кандидаты: база b0, перекрёстные cA_/cB_ стороны, топ-3 стороны. Три чтения каждого: lat 0 (без суффикса, справочно), `_lx4` (lat = latx = 4 — основное, §13.4 п. 8), `_lx200` (справочно).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from lib import ROOT, SIDES  # noqa: E402


def read_lines():
    d = {}
    for f in os.listdir(ROOT):
        if f.startswith("variants-") and f.endswith(".txt") and f not in ("variants-tick.txt", "variants-t544.txt"):
            for ln in open(os.path.join(ROOT, f), encoding="utf-8"):
                ln = ln.strip()
                if ln:
                    d.setdefault(ln.split()[0], ln)
    return d


def main():
    lines = read_lines()
    top = json.load(open(os.path.join(ROOT, "top3.json")))
    ids = ["b0"]
    for side in SIDES:
        ids += ["cA_" + side, "cB_" + side] + [t["vid"] for t in top[side]]
    out, seen = [], set()
    for i in ids:
        if i in seen or (i != "b0" and i not in lines):
            continue
        seen.add(i)
        params = lines[i].split()[1:] if i in lines else []
        out.append(" ".join([i] + params))
        out.append(" ".join([i + "_lx4"] + params + ["lat=4", "latx=4"]))
        out.append(" ".join([i + "_lx200"] + params + ["lat=200", "latx=200"]))
    with open(os.path.join(ROOT, "variants-t544.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print("\n".join(out))


if __name__ == "__main__":
    main()
