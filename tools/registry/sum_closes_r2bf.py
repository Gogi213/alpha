#!/usr/bin/env python3
"""TK-190: сводка closes-r2bf-cap{0,3}-<авг|сен>.json (psim R2-B с 3 досчитанными символо-сутками) -> csv variant,month,cap,n,sum_usd."""
import json, sys
d, out = sys.argv[1], sys.argv[2]
with open(out, "w", encoding="utf-8", newline="\n") as f:
    f.write("variant,month,cap,n,sum_usd\n")
    for m in (8, 9):
        for cap in (0, 3):
            for v, per in json.load(open(f"{d}/closes-r2bf-cap{cap}-2026-{m:02d}.json")).items():
                for mo, caps in per.items():
                    for c, tr in caps.items():
                        f.write(f"{v},{mo},{c},{len(tr)},{sum(x[1] for x in tr):.4f}\n")
