#!/usr/bin/env python3
"""TK-461 D2a-сравнение (P-TK461 §5, В-72): база выходов против C-X. По каждой ячейке (tf, ev, dir, stop, take, par; side
однозначно по ev×dir, т.е. разрез ТФ × лонг/шорт в ключе) парная Δ = net события − среднее net его контролей (evk = t0 события),
интервал по дням UTC (как tk461_agg), по годам, Холм по всем ячейкам. Потоком по монетам.
python tk461_d2a.py <scan_dir> <ctrl_dir> <out_cells.csv> <out_years.csv> [процессы] [маска]"""
import glob
import os
import sys
from multiprocessing import Pool
import numpy as np
import pandas as pd
from math import erfc, sqrt

REJ = {"nofill", "no_target"}
KEY = ["tf", "ev", "dir", "stop", "take", "par"]
SUMS = ["c", "d", "b", "k", "d2"]


def one(a):
    fs, fc = a
    s = pd.read_csv(fs)
    c = pd.read_csv(fc)
    s = s[~s.reason.isin(REJ)]
    c = c[~c.reason.isin(REJ)]
    if not len(s) or not len(c):
        return None
    s = s.assign(net=s.gross - s.fee - s.fund)
    c = c.assign(net=c.gross - c.fee - c.fund, ev=c.ev.str[2:])
    cm = c.groupby(KEY + ["evk"], observed=True).net.mean().rename("k").reset_index().rename(columns={"evk": "t0"})
    m = s[KEY + ["sym", "t0", "side", "net"]].merge(cm, on=KEY + ["t0"], how="inner")
    m["d"] = m.net - m.k
    m["b"] = m.net
    m["d2"] = m.d ** 2
    m["c"] = 1
    m["day"] = (m.t0 // 86400000).astype(np.int32)
    g = m.groupby(KEY + ["day"], observed=True)[SUMS].sum().reset_index()
    sy = m[KEY + ["sym", "side"]].drop_duplicates()
    for k in KEY:
        g[k] = g[k].astype("category")
        sy[k] = sy[k].astype("category")
    return g, sy


def main(sd, cd, oc, oy, nproc="12", pat="*.csv.gz"):
    jobs = []
    for f in sorted(glob.glob(os.path.join(sd, pat))):
        fc = os.path.join(cd, os.path.basename(f))
        if os.path.exists(fc):
            jobs.append((f, fc))
    D = None
    S = None
    buf, sbuf = [], []
    with Pool(int(nproc)) as p:
        for i, r in enumerate(p.imap_unordered(one, jobs, chunksize=1)):
            if r is not None:
                buf.append(r[0])
                sbuf.append(r[1])
            if len(buf) >= 12 or i == len(jobs) - 1:
                if buf:
                    X = pd.concat(([D] if D is not None else []) + buf, ignore_index=True)
                    D = X.groupby(KEY + ["day"], observed=True)[SUMS].sum().reset_index()
                    Y = pd.concat(([S] if S is not None else []) + sbuf, ignore_index=True)
                    S = Y.drop_duplicates()
                    buf, sbuf = [], []
                    print("files", i + 1, "of", len(jobs), flush=True)
    nsym = S.groupby(KEY, observed=True).sym.nunique().rename("syms")
    side = S.groupby(KEY, observed=True).side.first().rename("side")
    D["year"] = pd.to_datetime(D.day.astype(np.int64) * 86400000, unit="ms").dt.year
    D["sc"] = D.d * D.c
    D["c2"] = D.c.astype(np.float64) ** 2
    g = D.groupby(KEY, observed=True)
    A = g[["c", "d", "b", "k", "d2", "sc", "c2"]].sum()
    A["days"] = g.size()
    A = A.join(nsym).join(side)
    n = A.c
    m = A.d / n
    ss_ = A.d2 - 2 * m * A.sc + m ** 2 * A.c2
    se = np.sqrt(np.maximum(ss_, 0)) / n
    z = (m / se.replace(0, np.nan)).abs()
    p = z.map(lambda v: erfc(v / sqrt(2)) if v == v else 1.0)
    cells = pd.DataFrame({"side": A.side, "n": n.astype(int), "syms": A.syms, "days": A.days, "base": A.b / n, "ctrl": A.k / n,
                          "delta": m, "lo": m - 1.96 * se, "hi": m + 1.96 * se, "p": p,
                          "small": ((n < 8) | (A.syms < 5)).astype(int)}).reset_index()
    o = cells.p.rank(method="first").astype(int)
    K = len(cells)
    srt = cells.assign(r=o).sort_values("r")
    # Холм: p_(i)*(K-i) (i с 0), скользящий максимум вперёд
    adj = np.minimum(1.0, np.maximum.accumulate(srt.p.values * (K - np.arange(K))))
    cells.loc[srt.index, "p_holm"] = adj
    Yr = D.groupby(KEY + ["year"], observed=True)[["c", "d", "b"]].sum().reset_index()
    years = Yr[KEY + ["year"]].assign(n=Yr.c, delta=Yr.d / Yr.c, base=Yr.b / Yr.c)
    cells.to_csv(oc, index=False)
    years.to_csv(oy, index=False)
    print("cells", K, "events-pairs", int(n.sum()))


if __name__ == "__main__":
    main(*sys.argv[1:])
