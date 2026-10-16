#!/usr/bin/env python3
"""TK-196: сводка closes-f1-cap{0,3}-<мес>.json (F1 П-14, psim v171c) -> csv variant,month,cap,n,sum_usd. Без пересчёта (В-213).
  sum_closes_f1.py <каталог psim> <out.csv> [префикс closes-<префикс>-cap…, умолч. f1; TK-266: g117d]"""
import json, sys
d, out = sys.argv[1], sys.argv[2]
pre = sys.argv[3] if len(sys.argv) > 3 else "f1"
with open(out, "w", encoding="utf-8", newline="\n") as f:
    f.write("variant,month,cap,n,sum_usd\n")
    for m in range(1, 10):
        for cap in (0, 3):
            for v, per in json.load(open(f"{d}/closes-{pre}-cap{cap}-2026-{m:02d}.json")).items():
                for mo, caps in per.items():
                    for c, tr in caps.items():
                        f.write(f"{v},{mo},{c},{len(tr)},{sum(x[1] for x in tr):.4f}\n")
