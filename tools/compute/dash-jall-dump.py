#!/usr/bin/env python3
"""TK-018: выгрузка июля всех вариантов для дашборда — строка ps.json эпохи «июль» и закрытия каждого прогона
`p07-all-jul-read.py` (`tmp-p07/jall-read/<имя>/ps.json`, `ps-closes.json`; свои суммы не считаются) + его итог
(`jall-read.json`: kpi, usd, diff_main_usd). Схема строки — как `dash-jul-dump.py`.

    python3 dash-jall-dump.py > ~/alpha/tmp-dash/jall.json      # на Steam Deck
"""
import json
import os

A = os.path.expanduser("~/alpha")
R = os.path.join(A, "tmp-p07/jall-read")
res = json.load(open(os.path.join(R, "jall-read.json"), encoding="utf-8"))
out = {"_meta": res["_meta"]}
for name, r in res.items():
    if name == "_meta":
        continue
    rec = {"read": r}
    d = os.path.join(R, name)
    if "undefined" not in r and os.path.exists(os.path.join(d, "ps.json")):
        P = json.load(open(os.path.join(d, "ps.json"), encoding="utf-8"))
        co = json.load(open(os.path.join(d, "ps-closes.json"), encoding="utf-8"))
        v = P["variants"][0]
        grid = [g for g in P["grid"] if g["epoch"] == "июль"]
        assert len(grid) == 1, (name, len(grid))
        g = grid[0]
        closes = co[v["name"]]["июль"][str(g["max_pos"])]
        assert g["n"] == len(closes) == r["n_trades"], (name, g["n"], len(closes), r["n_trades"])
        rec.update(form=v["form"], set=v["set"], max_pos=g["max_pos"], grid=g, closes=closes)
    out[name] = rec
print(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
