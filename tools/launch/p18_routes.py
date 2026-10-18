#!/usr/bin/env python3
"""TK-289 (П-18 F1): маршруты хешей осей 1–5, 6а, 7 для панели — add-routes.json {routes, lines, monthly, sets} (как p17b-dep).
Вариант клетки по месяцу — «состояние@форма», состояние p18a<ось>v<знач>-<мес> (набор — SET_B1 + btc4h_max/usd_min, p18_plan.cell); хеш — p18_scope.cfg_of.
Хеши, уже имеющие маршрут в routes.json (ttl 300–1200 — Г-129), пропускаются.  python tools/launch/p18_routes.py <routes.json> <out add-routes.json>"""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "registry"))
sys.path.insert(0, os.path.join(HERE, "..", "compute"))
import cellhash as ch, p18_plan as pl, p18_scope as sc

have = json.load(open(sys.argv[1], encoding="utf-8"))["routes"]
out = {"routes": {}, "lines": {}, "monthly": {}, "sets": {}}
for ax in ("1", "2", "3", "4", "5", "6a", "7"):
    for v in pl.AX[ax][1]:
        h = ch.cell_hash(ch.normalize(sc.cfg_of(ax, v)))
        if h in have or h in out["routes"]:
            continue
        out["routes"][h] = "monthly"; out["monthly"][h] = {}
        for ym in pl.MONTHS:
            tup, state, sset = pl.cell(ax, v, ym, {})
            out["monthly"][h][ym] = f"{tup} {state}"
            out["sets"][state] = sset.split(":", 1)[1]
json.dump(out, open(sys.argv[2], "w", encoding="utf-8", newline="\n"), ensure_ascii=False, sort_keys=True)
print({k: len(v) for k, v in out.items()})
