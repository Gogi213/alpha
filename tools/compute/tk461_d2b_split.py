#!/usr/bin/env python3
"""TK-461 D2b: разрез монета × сигнал(ev,dir) × ТФ × сторона — лучший выход, по годам и по месяцам (владелец 31.10 00:16/00:19).
Вход — готовые d2b-out/<SYM>-<tf>.csv.gz (суммы c,d,b,k,d2 по ячейке выхода и дню UTC), без пересчёта ядра.
Срез = (sym, tf, ev, dir, side); лучший выход = max base (net после издержек) среди ячеек с n>=NMIN; отбор по всему периоду,
поэтому колонка p_bonf_exit = p(Δ) лучшей × число ячеек среза, p_holm_slice = Холм по всем срезам (методика Судьи — предварительно).
python tk461_d2b_split.py <dir[,dir..]> <out_prefix> [процессов] [маска]
Выходы: <prefix>-slices.csv (срез, лучший выход, n, base, Δ, годы, месяцы в плюсе, поправки), <prefix>-months.csv (долго: срез × месяц),
<prefix>-coins.csv (монета: срезов, срезов в плюсе, лучший срез)."""
import glob
import os
import sys
from math import erfc, sqrt
from multiprocessing import Pool

import numpy as np
import pandas as pd

KEY = ["tf", "ev", "dir", "stop", "take", "par", "be_r", "ot", "otp"]
SL = ["sym", "tf", "ev", "dir", "side"]
NMIN = 8


def pval(m, se):
    return erfc(abs(m / se) / sqrt(2)) if se > 0 else 1.0


def one(f):
    x = pd.read_csv(f, low_memory=False)
    if not len(x):
        return None
    for k in ("be_r", "ot", "otp", "par"):
        x[k] = x[k].astype(str).replace("nan", "")
    x = x[x.c > 0]
    g = x.groupby(SL + KEY[3:] + ["day"], observed=True)[["c", "d", "b", "k", "d2"]].sum().reset_index()
    g["sc"] = g.d * g.c
    g["c2"] = g.c.astype(np.float64) ** 2
    ck = SL + KEY[3:]
    A = g.groupby(ck, observed=True)[["c", "d", "b", "k", "d2", "sc", "c2"]].sum()
    n = A.c
    m = A.d / n
    se = np.sqrt(np.maximum(A.d2 - 2 * m * A.sc + m ** 2 * A.c2, 0)) / n
    A = A.assign(base=A.b / n, ctrl=A.k / n, delta=m, se=se).reset_index()
    A["plus"] = (A.base > 0).astype(int)
    sl_n = A.groupby(SL, observed=True).agg(k_exits=("c", "size"), exits_plus=("plus", "sum"))
    cand = A[A.c >= NMIN]
    if not len(cand):
        return None
    best = cand.loc[cand.groupby(SL, observed=True).base.idxmax()].set_index(SL)
    best = best.join(sl_n)
    bk = best.reset_index()[ck]
    G = g.merge(bk, on=ck, how="inner")
    G["month"] = pd.to_datetime(G.day.astype(np.int64) * 86400000, unit="ms").dt.strftime("%Y-%m")
    G["year"] = G.month.str[:4]
    M = G.groupby(SL + ["month"], observed=True)[["c", "d", "b"]].sum().reset_index()
    M = M.assign(n=M.c, base=M.b / M.c, delta=M.d / M.c)[SL + ["month", "n", "base", "delta"]]
    Y = G.groupby(SL + ["year"], observed=True)[["c", "b"]].sum().reset_index()
    Y = Y.assign(base=Y.b / Y.c)
    yr = Y.groupby(SL, observed=True).agg(years=("year", "size"), years_plus=("base", lambda s: int((s > 0).sum())))
    mo = M.groupby(SL, observed=True).agg(months=("month", "size"), months_plus=("base", lambda s: int((s > 0).sum())))
    yw = Y.pivot_table(index=SL, columns="year", values="base").add_prefix("y")
    B = best.join(yr).join(mo).join(yw).reset_index()
    B["p"] = [pval(a, b) for a, b in zip(B.delta, B.se)]
    B["p_bonf_exit"] = np.minimum(1.0, B.p * B.k_exits)
    B["lo"] = B.delta - 1.96 * B.se
    B["hi"] = B.delta + 1.96 * B.se
    B = B.rename(columns={"c": "n"})
    return B, M


def main(dirs, pref, nproc="8", pat="*.csv.gz"):
    jobs = sorted(f for d in dirs.split(",") for f in glob.glob(os.path.join(d, pat)))
    Bs, Ms = [], []
    with Pool(int(nproc)) as p:
        for i, r in enumerate(p.imap_unordered(one, jobs, chunksize=1)):
            if r is not None:
                Bs.append(r[0])
                Ms.append(r[1])
            if i % 50 == 0:
                print("files", i + 1, "of", len(jobs), flush=True)
    B = pd.concat(Bs, ignore_index=True)
    M = pd.concat(Ms, ignore_index=True)
    K = len(B)
    srt = B.sort_values("p_bonf_exit", kind="stable")
    B.loc[srt.index, "p_holm_slice"] = np.minimum(1.0, np.maximum.accumulate(srt.p_bonf_exit.values * (K - np.arange(K))))
    ycols = sorted(c for c in B.columns if c.startswith("y") and c[1:].isdigit())
    cols = SL + ["stop", "take", "par", "be_r", "ot", "otp", "n", "base", "ctrl", "delta", "lo", "hi", "k_exits", "exits_plus",
                 "years", "years_plus", "months", "months_plus", "p_bonf_exit", "p_holm_slice"] + ycols
    B = B[cols].sort_values(["base"], ascending=False)
    B.to_csv(pref + "-slices.csv", index=False, float_format="%.4g")
    M.sort_values(SL + ["month"]).to_csv(pref + "-months.csv", index=False, float_format="%.4g")
    C = B.groupby("sym").agg(slices=("base", "size"), slices_plus=("base", lambda s: int((s > 0).sum())),
                             best_base=("base", "max"), plus_all_years=("years_plus", lambda s: 0))
    pa = B[(B.years > 0) & (B.years == B.years_plus)].groupby("sym").size()
    C["plus_all_years"] = pa.reindex(C.index).fillna(0).astype(int)
    C.sort_values(["slices_plus", "best_base"], ascending=False).to_csv(pref + "-coins.csv", float_format="%.4g")
    print("slices", K, "coins", len(C), "slices base>0", int((B.base > 0).sum()), "coins with plus", int((C.slices_plus > 0).sum()))


if __name__ == "__main__":
    main(*sys.argv[1:])
