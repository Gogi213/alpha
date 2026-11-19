"""TK-620: генератор замороженных клеток §13.31 из значений шага 0 (`tk620_step0.py --json`).
  python tk620_cells.py --long <long.json> --short <short.json> --out-cells docs/research/freeze/tk620-cells.json --out-variants docs/research/freeze/tk620-variants.txt
Клетка = база стороны + патч (§13.31 п. 3): `nx` × 3, `sl` × 3, `cs` (cw = 60, cl × ch × cm = 2 × 2 × 2, запасной `sl` = средняя клетка сетки) — 14 на сторону, 28 всего;
строки баз `L_base`, `S_base` — контроль (не испытание). Соседи для плато: по оси сетки (`nx`/`sl`: соседние значения; `cs`: клетки, отличающиеся одной осью)."""
import argparse
import hashlib
import itertools
import json

LB = "lat=4 latx=4 pk=1 pb=1 fh=4 fk=120 fm=50 fb=0.5 ew=5 ex=0 rf=0.2 rs=20"
SB = "lat=4 latx=4 pk=1 pb=1 fh=4 fk=120 fm=50 fb=0.5 ew=5 ex=0 tt=20 tb=0.1 rf=0.2 rs=2 bm=1 e=0.15 e2=0.05"
BASE = {"long": ("L_base", LB), "short": ("S_base", SB)}
CW = 60


def g(x):
    return ("%.3f" % x).rstrip("0").rstrip(".")


def side_cells(side, st):
    bid, bline = BASE[side]
    pre = bid[0]
    lv, mg, wq = st["levels"]["grid"], st["margin"]["grid"], {int(k): v for k, v in st["width_q"].items()}
    mid = lv[1]
    cells = []
    for fam in ("nx", "sl"):
        for i, x in enumerate(lv):
            cid = "%s_%s_%s" % (pre, fam, g(x))
            nb = ["%s_%s_%s" % (pre, fam, g(lv[j])) for j in (i - 1, i + 1) if 0 <= j < 3]
            cells.append(dict(id=cid, side=side, family=fam, patch="%s=%s" % (fam, g(x)), neighbours=nb))
    axes = [("cl", (10, 25)), ("ch", (75, 90)), ("cm", (0, 1))]
    combos = list(itertools.product(range(2), range(2), range(2)))
    def cid_of(c):
        return "%s_cs_q%d_q%d_m%d" % (pre, axes[0][1][c[0]], axes[1][1][c[1]], c[2])
    for c in combos:
        patch = "cw=%d cl=%s ch=%s cm=%s sl=%s" % (CW, g(wq[axes[0][1][c[0]]]), g(wq[axes[1][1][c[1]]]), g(mg[c[2]]), g(mid))
        nb = []
        for ax in range(3):
            d = list(c)
            d[ax] ^= 1
            nb.append(cid_of(tuple(d)))
        cells.append(dict(id=cid_of(c), side=side, family="cs", patch=patch, neighbours=nb))
    for x in cells:
        x["line"] = "%s %s %s" % (x["id"], bline, x["patch"])
    return cells


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--long", required=True)
    ap.add_argument("--short", required=True)
    ap.add_argument("--out-cells", required=True)
    ap.add_argument("--out-variants", required=True)
    a = ap.parse_args()
    st = {"long": json.load(open(a.long, encoding="utf-8")), "short": json.load(open(a.short, encoding="utf-8"))}
    cells = side_cells("long", st["long"]) + side_cells("short", st["short"])
    assert len(cells) == 28 and len({c["id"] for c in cells}) == 28
    lines = ["%s %s" % (BASE[s][0], BASE[s][1]) for s in ("long", "short")] + [c["line"] for c in cells]
    open(a.out_variants, "w", encoding="utf-8", newline="\n").write("\n".join(lines) + "\n")
    sha = hashlib.sha256(open(a.out_variants, "rb").read()).hexdigest()
    json.dump({"version": "tk620-cells-1", "variants_sha256": sha, "base": {"long": "L_base", "short": "S_base"}, "step0": st,
               "cells": [{k: v for k, v in c.items() if k != "line"} for c in cells]},
              open(a.out_cells, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1)
    print("cells", len(cells), "variants sha256", sha)


if __name__ == "__main__":
    main()
