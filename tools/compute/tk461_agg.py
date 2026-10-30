#!/usr/bin/env python3
"""TK-461 шаг D2a-сводка: scan/<SYM>-<tf>.csv.gz -> по ячейке (tf, ev, dir, stop, take, par): сделок, монет, доля выигрышей, ожидание bps
gross / net (смесь) / net (все тейкер) с фандингом, интервал (по дням UTC, нормальный, описательно — вердикт только после C-X и Холма),
по годам (n, net). Описательно: контроля C-X тут нет, вердикта нет.
python tk461_agg.py <scan_dir> <out_cells.csv> <out_years.csv>"""
import glob
import os
import sys
import numpy as np
import pandas as pd

REJ = {"nofill", "no_target"}


def load(d):
    fr = []
    for f in sorted(glob.glob(os.path.join(d, "*.csv.gz"))):
        x = pd.read_csv(f)
        if len(x):
            fr.append(x)
    return pd.concat(fr, ignore_index=True)


def main(d, oc, oy):
    df = load(d)
    n0 = len(df)
    df = df[~df.reason.isin(REJ)].copy()
    df["net"] = df.gross - df.fee - df.fund
    df["net_tk"] = df.gross - df.fee_tk - df.fund
    df["win"] = (df.net > 0).astype(float)
    df["day"] = (df.t0 // 86400000).astype(int)
    df["year"] = pd.to_datetime(df.t0, unit="ms").dt.year
    key = ["tf", "ev", "dir", "stop", "take", "par"]
    out, yrs = [], []
    for k, g in df.groupby(key):
        dm = g.groupby("day").net.agg(["sum", "count"])
        n = len(g)
        m = g.net.mean()
        # кластер-день: SE среднего по дням
        r = dm["sum"] - m * dm["count"]
        se = np.sqrt((r ** 2).sum()) / n
        out.append(dict(zip(key, k), n=n, syms=g.sym.nunique(), days=len(dm), win=g.win.mean(), gross=g.gross.mean(),
                        net=m, net_tk=g.net_tk.mean(), lo=m - 1.96 * se, hi=m + 1.96 * se, small=int(n < 8 or g.sym.nunique() < 5)))
        for y, gy in g.groupby("year"):
            yrs.append(dict(zip(key, k), year=y, n=len(gy), net=gy.net.mean(), gross=gy.gross.mean()))
    pd.DataFrame(out).to_csv(oc, index=False)
    pd.DataFrame(yrs).to_csv(oy, index=False)
    print("rows", n0, "kept", len(df), "cells", len(out))


if __name__ == "__main__":
    main(*sys.argv[1:4])
