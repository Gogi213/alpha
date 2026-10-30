#!/usr/bin/env python3
"""TK-467 гейт: вывод нового драйвера (tk467_d2b_run.py) против прежнего (tk461_d2b_run.py) по паре: те же группы, c — точно, d,b,k,d2 — rtol 1e-9.
python tk467_gate.py <старый.csv.gz> <новый.csv.gz>   -> «IDENTICAL_D2B» rc 0, иначе «DIFF ...» rc 1"""
import sys

import numpy as np
import pandas as pd

SUMS = ["c", "d", "b", "k", "d2"]
a, b = (pd.read_csv(p, dtype={"ot": str}) for p in sys.argv[1:3])
if list(a.columns) != list(b.columns):
    print("DIFF колонки", list(a.columns), list(b.columns)); sys.exit(1)
keys = [c for c in a.columns if c not in SUMS]
for d in (a, b):
    for c in keys:
        if d[c].dtype == object:
            d[c] = d[c].fillna("")
if len(a) != len(b) or a.duplicated(keys).any() or b.duplicated(keys).any():
    print("DIFF строк", len(a), len(b), "дубли", a.duplicated(keys).sum(), b.duplicated(keys).sum()); sys.exit(1)
m = a.merge(b, on=keys, how="outer", suffixes=("_a", "_b"), indicator=True)
if (m._merge != "both").any():
    print("DIFF группы", (m._merge != "both").sum()); sys.exit(1)
bad = (m.c_a != m.c_b).sum()
for c in SUMS[1:]:
    bad += (~np.isclose(m[c + "_a"], m[c + "_b"], rtol=1e-9, atol=1e-9)).sum()
if bad:
    print("DIFF значений", bad); sys.exit(1)
print(f"IDENTICAL_D2B строк {len(a)}, c={int(a.c.sum())}")
