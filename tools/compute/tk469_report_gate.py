#!/usr/bin/env python3
"""TK-469 гейт report: cells/years старой и быстрой версии -- ключи и целые поля точно, дробные rtol 1e-9. python tk469_report_gate.py <old_cells> <new_cells> <old_years> <new_years>"""
import sys

import numpy as np
import pandas as pd

ok = True
for a, b in ((sys.argv[1], sys.argv[2]), (sys.argv[3], sys.argv[4])):
    x, y = pd.read_csv(a, keep_default_na=False), pd.read_csv(b, keep_default_na=False)
    if list(x.columns) != list(y.columns) or len(x) != len(y):
        print("DIFF shape", a, x.shape, y.shape)
        ok = False
        continue
    bad = []
    for c in x.columns:
        if x[c].dtype.kind == "f":
            if not np.allclose(x[c], y[c], rtol=1e-9, atol=0, equal_nan=True):
                bad.append(c)
        elif not (x[c] == y[c]).all():
            bad.append(c)
    same = (x.to_csv(index=False) == y.to_csv(index=False))
    print("IDENTICAL_BYTES" if same else ("RTOL_OK" if not bad else "DIFF " + ",".join(bad)), a, len(x))
    ok = ok and not bad
sys.exit(0 if ok else 1)
