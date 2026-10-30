#!/usr/bin/env python3
"""TK-466: сверка флагов tk461_s_events (s1/s2/s4) с принятыми sm_s2.one / sm_s4.one на l2-таблице монеты (по ключу dir,tj=t).
python test_tk461_s_events.py <out s-файл s2|s4 csv.gz> <kind s2|s4> <l2-файл> <каталог минут> ; расхождение -> rc 1."""
import importlib.util, os, sys
import numpy as np, pandas as pd
H = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [H, os.path.join(H, ".."), "/data/tk0451"]
kind, outf, l2f, src = sys.argv[1:5]
m = importlib.util.spec_from_file_location(kind, os.path.join(H, "..", "sm_" + kind + ".py")); M = importlib.util.module_from_spec(m); m.loader.exec_module(M)
ref = M.one((l2f, src)); tf = int(os.path.basename(l2f).split("-")[2])
mine = pd.read_csv(outf); mine = mine[mine.tf == tf]; mine["age"] = mine.j - mine.i; ref = ref.drop_duplicates(["dir", "t", "age"])
cols = {"s2": [("pa", "pa"), ("pb", "pb")], "s4": [("pD1", "pD1"), ("pW1", "pW1")]}[kind]
x = ref.merge(mine, left_on=["dir", "t", "age"], right_on=["dir", "tj", "age"], suffixes=("_r", ""))
bad = 0
for r, c in cols:
    if r not in ref or c not in mine: continue
    a, b = x[r + "_r" if r + "_r" in x else r].values, x[c].values
    d = int((~((a == b) | (np.isnan(a) & np.isnan(b)))).sum()); bad += d
    print(kind, r, "n", len(x), "расхождений", d)
sys.exit(1 if bad or not len(x) else 0)
