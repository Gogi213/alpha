#!/usr/bin/env python3
"""TK-315: маршруты 383 хешей П-17 F2 (monthly — Г-81/101/135 по порогу месяца, cells — Г-136/137 набор) -> routes-p17f2.json
(routes/lines/monthly/sets, как add-routes.json для tk279-deploy-delta.py). Строки — master.p17_cell с порогами P-17-thresholds{,-f2}.csv.
  python tools/compute/archive/tk315-make-routes.py <f2-cells.jsonl> <out.json>"""
import json, os, sys
R = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "registry")
sys.path.insert(0, R)
import cellhash as ch, master, p17_scope as sc
routes, lines, monthly, used = {}, {}, {}, set()
for l in open(sys.argv[1], encoding="utf-8"):
    x = json.loads(l); cfg = sc.cfg_of()
    master.p17_cell(x["hyp"], dict(x.get("axes_full") or x["axes"]), cfg, x.get("form") or sc.BASE)
    h = ch.cell_hash(cfg); assert h == x["hash"]
    if h in master.MONTHLY:
        routes[h] = "monthly"; monthly[h] = master.MONTHLY[h]; used |= {ln.split()[1] for ln in monthly[h].values()}
    elif master.LINES[h][0]:
        routes[h] = "cells"; lines[h] = master.LINES[h][0]; used.add(lines[h].split()[1])
    else:
        routes[h] = "noline"
doc = {"protocol": "P-17", "fraction": 2, "routes": routes, "lines": lines, "monthly": monthly, "sets": {u: master.MSETS[u] for u in sorted(used)}}
open(sys.argv[2], "w", encoding="utf-8", newline="\n").write(json.dumps(doc, ensure_ascii=False, sort_keys=True))
import collections; print(collections.Counter(routes.values()), len(used), "наборов")
