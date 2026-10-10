#!/usr/bin/env python3
"""TK-017: выгрузка июля для дашборда — строка ps.json эпохи «июль» и закрытия пяти форм TK-010
(`tmp-p07/jul-read/<форма>/ps.json`, `ps-closes.json` — прогоны `p07-jul-read.py --out`, свои суммы не считаются).

    python3 dash-jul-dump.py > ~/alpha/tmp-dash/jul.json      # на Steam Deck

Выход: {"<форма>": {"form", "set", "max_pos", "grid": строка «июль», "closes": [...]}}.
"""
import glob
import json
import os

A = os.path.expanduser("~/alpha")
out = {}
for d in sorted(glob.glob(os.path.join(A, "tmp-p07/jul-read/*/ps.json"))):
    d = os.path.dirname(d)
    P = json.load(open(os.path.join(d, "ps.json"), encoding="utf-8"))
    co = json.load(open(os.path.join(d, "ps-closes.json"), encoding="utf-8"))
    v = P["variants"][0]
    grid = [g for g in P["grid"] if g["epoch"] == "июль"]
    assert len(grid) == 1, (d, len(grid))
    g = grid[0]
    closes = co[v["name"]]["июль"][str(g["max_pos"])]
    assert g["n"] == len(closes), (d, g["n"], len(closes))
    out[os.path.basename(d)] = {"form": v["form"], "set": v["set"], "max_pos": g["max_pos"], "grid": g, "closes": closes}
print(json.dumps(out, ensure_ascii=False))
