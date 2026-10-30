#!/usr/bin/env python3
"""TK-461 шаг D2a-сводка (потоком, TK-461 решение Судьи 30.10): scan/<SYM>-<tf>.csv.gz -> по ячейке (tf, ev, dir, stop, take, par):
сделок, монет, доля выигрышей, ожидание bps gross / net (смесь) / net (все тейкер) с фандингом, интервал (по дням UTC, нормальный,
описательно — вердикт только после C-X и Холма), по годам. Результат тот же, что у tk461_agg_v1.py, память — малые кадры вместо всех строк.
python tk461_agg.py <scan_dir> <out_cells.csv> <out_years.csv> [маска файлов]"""
import glob
import os
import sys
import numpy as np
import pandas as pd

REJ = {"nofill", "no_target"}
KEY = ["tf", "ev", "dir", "stop", "take", "par"]
SUMS = ["c", "net", "net_tk", "gross", "win", "net2"]


def one(f):
    x = pd.read_csv(f)
    x = x[~x.reason.isin(REJ)]
    if not len(x):
        return None, None
    x = x.assign(net=x.gross - x.fee - x.fund, net_tk=x.gross - x.fee_tk - x.fund, day=(x.t0 // 86400000).astype(np.int32))
    x["win"] = (x.net > 0).astype(np.float64)
    x["c"] = 1
    d = x.groupby(KEY + ["day"], observed=True)[["c", "net", "net_tk", "gross", "win"]].sum().reset_index()
    s = x[KEY + ["sym"]].drop_duplicates()
    for k in KEY:
        d[k] = d[k].astype("category")
        s[k] = s[k].astype("category")
    return d, s


def main(d, oc, oy, pat="*.csv.gz"):
    ds, ss, n0 = [], [], 0
    for f in sorted(glob.glob(os.path.join(d, pat))):
        a, s = one(f)
        if a is not None:
            ds.append(a)
            ss.append(s)
            n0 += int(a.c.sum())
    D = pd.concat(ds, ignore_index=True)
    D = D.groupby(KEY + ["day"], observed=True)[["c", "net", "net_tk", "gross", "win"]].sum().reset_index()
    S = pd.concat(ss, ignore_index=True).drop_duplicates()
    nsym = S.groupby(KEY, observed=True).sym.nunique().rename("syms")
    D["year"] = pd.to_datetime(D.day.astype(np.int64) * 86400000, unit="ms").dt.year
    D["s2"] = D.net ** 2
    D["sc"] = D.net * D.c
    D["c2"] = D.c.astype(np.float64) ** 2
    g = D.groupby(KEY, observed=True)
    A = g[["c", "net", "net_tk", "gross", "win", "s2", "sc", "c2"]].sum()
    A["days"] = g.size()
    A = A.join(nsym)
    n = A.c
    m = A.net / n
    ss_ = A.s2 - 2 * m * A.sc + m ** 2 * A.c2
    se = np.sqrt(np.maximum(ss_, 0)) / n
    cells = pd.DataFrame({"n": n.astype(int), "syms": A.syms, "days": A.days, "win": A.win / n, "gross": A.gross / n, "net": m,
                          "net_tk": A.net_tk / n, "lo": m - 1.96 * se, "hi": m + 1.96 * se,
                          "small": ((n < 8) | (A.syms < 5)).astype(int)}).reset_index()
    Y = D.groupby(KEY + ["year"], observed=True)[["c", "net", "gross"]].sum().reset_index()
    years = Y[KEY + ["year"]].assign(n=Y.c, net=Y.net / Y.c, gross=Y.gross / Y.c)
    cells.to_csv(oc, index=False)
    years.to_csv(oy, index=False)
    print("kept", n0, "cells", len(cells))


if __name__ == "__main__":
    main(*sys.argv[1:5])
