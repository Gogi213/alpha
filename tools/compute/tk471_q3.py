#!/usr/bin/env python3
"""TK-471 Q-3: ячейка x год с net при худшем (все ноги тейкер) и лучшем (все мейкер) конце тарифа из выхода ядра --legs 1
(столбцы c,b,bt,bm по (sym, ключ, день); P-TK461 §6/§11). net = sum(<b|bt|bm>)/sum(c), суммы по дням как в tk461_d2b_report.
python tk471_q3.py <q3-out> <out_years.csv> <out_cells.csv> [процессы] [семья=каталог ...]
Гейт (если рядом лежат прежние years): python tk471_q3.py --gate <out_years.csv> <прежний years.csv> ..."""
import glob
import os
import sys
from multiprocessing import Pool

import numpy as np
import pandas as pd

KEY = ["tf", "ev", "dir", "stop", "take", "par", "be_r", "ot", "otp"]
SUMS = ["c", "b", "bt", "bm"]
USE = ["sym", "day"] + KEY[0:9] + SUMS


def strcol(s):
    codes, uniq = pd.factorize(s, use_na_sentinel=False)
    u = pd.Series(uniq).astype(str).replace("nan", "")
    return pd.Series(u.to_numpy()[codes], index=s.index)


def one(f):
    try:
        x = pd.read_csv(f, low_memory=False, usecols=USE)
    except pd.errors.EmptyDataError:  # ячейки подмножества не встретились в этой паре
        return None
    if not len(x):
        return None
    for k in ("be_r", "ot", "otp", "par"):
        x[k] = strcol(x[k])
    return x.groupby(KEY + ["day"], observed=True)[SUMS].sum().reset_index()


def family(d, nproc):
    jobs = sorted(glob.glob(os.path.join(d, "*.csv.gz")))
    D, buf = None, []
    with Pool(nproc) as p:
        for i, r in enumerate(p.imap_unordered(one, jobs, chunksize=1)):
            if r is not None:
                buf.append(r)
            if len(buf) >= 12 or i == len(jobs) - 1:
                if buf:
                    X = pd.concat(([D] if D is not None else []) + buf, ignore_index=True)
                    D = X.groupby(KEY + ["day"], observed=True)[SUMS].sum().reset_index()
                    buf = []
                    print(d, "files", i + 1, "of", len(jobs), flush=True)
    D = D[D.c > 0].copy()
    D["year"] = pd.to_datetime(D.day.astype(np.int64) * 86400000, unit="ms").dt.year
    Y = D.groupby(KEY + ["year"], observed=True)[SUMS].sum().reset_index()
    Y = Y.assign(n=Y.c, base=Y.b / Y.c, net_worst=Y.bt / Y.c, net_best=Y.bm / Y.c)
    return Y[KEY + ["year", "n", "base", "net_worst", "net_best"]]


def main(src, oy, oc, nproc="4", *fams):
    fams = fams or ["sancho", "bp", "bp3", "s"]
    ys = []
    for f in fams:
        name, _, dd = f.partition("=")
        Y = family(os.path.join(src, dd or name), int(nproc))
        ys.append(Y.assign(set=name))
    Y = pd.concat(ys, ignore_index=True)
    Y = Y[["set"] + KEY + ["year", "n", "base", "net_worst", "net_best"]]
    Y.to_csv(oy, index=False)
    g = Y.groupby(["set"] + KEY, observed=True)
    C = g.agg(years=("year", "size"), n=("n", "sum"), worst_min_year=("net_worst", "min"), best_min_year=("net_best", "min"),
              base_min_year=("base", "min")).reset_index()
    tot = Y.assign(bw=Y.net_worst * Y.n, bb=Y.net_best * Y.n, b0=Y.base * Y.n).groupby(["set"] + KEY, observed=True)[["bw", "bb", "b0", "n"]].sum()
    C = C.merge((tot[["bw", "bb", "b0"]].div(tot.n, axis=0)).rename(columns={"bw": "net_worst", "bb": "net_best", "b0": "base"}).reset_index(),
                on=["set"] + KEY)
    C["q3_worst_all_years_pos"] = (C.worst_min_year > 0).astype(int)
    C.to_csv(oc, index=False)
    print("years rows", len(Y), "cells", len(C), "Q3 pass", int(C.q3_worst_all_years_pos.sum()))


def gate(oy, *old):
    """base из q3 против base прежних years: ключ (tf,ev,dir,stop,take,par,be_r,ot,otp,year), строки как текст."""
    N = pd.read_csv(oy, dtype=str, keep_default_na=False)
    O = pd.concat([pd.read_csv(f, dtype=str, keep_default_na=False) for f in old], ignore_index=True)
    k = KEY + ["year"]
    M = N.merge(O, on=k, how="left", suffixes=("", "_old"), indicator=True)
    miss = (M._merge != "both").sum()
    M = M[M._merge == "both"]
    a, b = M.base.astype(float), M.base_old.astype(float)
    eq = int((M.base == M.base_old).sum())
    close = int(np.isclose(a, b, rtol=1e-9, atol=0).sum())
    nn = int((M.n.astype(float) == M.n_old.astype(float)).sum())
    print(f"gate: строк q3 {len(N)}, без пары в прежних {miss}, base текстом равны {eq}, rtol 1e-9 {close}, n равны {nn} из {len(M)}")
    return 0 if miss == 0 and close == len(M) and nn == len(M) else 1


if __name__ == "__main__":
    if sys.argv[1] == "--gate":
        sys.exit(gate(*sys.argv[2:]))
    main(*sys.argv[1:])
