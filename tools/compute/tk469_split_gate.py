#!/usr/bin/env python3
"""TK-469 гейт: tk461_d2b_split2.one() == tk461_d2b_split.one() на готовых d2b-out (точное равенство таблиц), + время.
python tk469_split_gate.py <файл.csv.gz> [...]   (рядом лежат оба модуля); rc 0 = IDENTICAL, 1 = различие."""
import sys
import time

import pandas as pd

import tk461_d2b_split as old
import tk461_d2b_split2 as new


def norm(df):
    df = df.copy()
    for c in df.columns:
        if str(df[c].dtype) in ("str", "string", "category"):
            df[c] = df[c].astype(object)
    return df.sort_values(list(df.columns), kind="stable").reset_index(drop=True)


bad = 0
for f in sys.argv[1:]:
    t = time.time()
    a = old.one(f)
    t1 = time.time() - t
    t = time.time()
    b = new.one(f)
    t2 = time.time() - t
    if a is None or b is None:
        ok = a is None and b is None
    else:
        ok = all(norm(x).equals(norm(y)) for x, y in zip(a, b))
        if not ok:
            for x, y in zip(a, b):
                x, y = norm(x), norm(y)
                print("shape", x.shape, y.shape, "dtypes_diff", [c for c in x.columns if c in y.columns and x[c].dtype != y[c].dtype][:5])
    print("IDENTICAL" if ok else "DIFF", f, "old %.1f s new %.1f s x%.2f" % (t1, t2, t1 / max(t2, 1e-9)), flush=True)
    bad += not ok
sys.exit(1 if bad else 0)
