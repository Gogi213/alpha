#!/usr/bin/env python3
"""TK-451 возврат 3 (Судья): слой 2 по P-TK451 §10 п.6 и поправке п.8: K=216, B=20000, seed 451. События НЕ пересчитываются:
вход — l2-days.csv.gz (x3-агрегатор), годы — готовый l2-layer2-years.csv.
python sancho_l2agg4.py <days.csv.gz> <years.csv> <out-prefix> [NX]"""
import sys
from multiprocessing import Pool
import numpy as np
import pandas as pd

B, CH = 20000, 4000
FAM_CL = ["A1", "A4", "A5", "A6", "A7", "A8"]
ANCHOR = {"x1", "x2", "x3m2lo", "x3m2hi"}  # X3 якорь m = 1·ATR14 (п.8 поправки)
KEY = ["claim", "oc", "ctl", "tf", "k", "dir"]
G = None


def boot(key):
    g = G[key]
    day, n, sr, sc = g
    N = n.sum()
    if N < 8 or len(day) < 5:
        return key, None
    D = len(day)
    d0 = (sr.sum() - sc.sum()) / N
    rng = np.random.default_rng(451)
    dd = []
    for _ in range(B // CH):
        cnt = rng.multinomial(D, np.full(D, 1 / D), CH).astype(np.float32)
        nn = cnt @ n.astype(np.float32)
        dd.append(((cnt @ (sr - sc).astype(np.float32)) / np.where(nn > 0, nn, np.nan)))
    dd = np.concatenate(dd)
    p = 2 * min((dd <= 0).mean(), (dd >= 0).mean()) + 1 / B
    return key, dict(N=int(N), days=D, p_ev=sr.sum() / N, p_ctl=sc.sum() / N, delta=d0,
                     lo=np.nanpercentile(dd, 2.5), hi=np.nanpercentile(dd, 97.5), p=min(p, 1.0))


if __name__ == "__main__":
    days, ypath, pre = sys.argv[1], sys.argv[2], sys.argv[3]
    nx = int(sys.argv[4]) if len(sys.argv) > 4 else 15
    A = pd.read_csv(days, usecols=KEY + ["sym", "day", "n", "sr", "sc", "real_n_all"])
    meta = A.groupby(KEY).agg(nsym=("sym", "nunique")).reset_index()
    rn = A.drop_duplicates(KEY + ["sym"]).groupby(KEY).real_n_all.sum().rename("real_n").reset_index()
    meta = meta.merge(rn, on=KEY)
    D = A.groupby(KEY + ["day"], as_index=False)[["n", "sr", "sc"]].sum()
    del A
    G = {}
    for key, g in D.groupby(KEY, sort=False):
        G[key] = (g.day.values, g.n.values.astype(np.float64), g.sr.values.astype(np.float64), g.sc.values.astype(np.float64))
    del D
    with Pool(nx) as p:
        res = p.map(boot, list(G), chunksize=1)
    R = pd.DataFrame([dict(zip(KEY, k), **r) for k, r in res if r]).merge(meta, on=KEY)
    w = R.pivot_table(index=["claim", "tf", "k", "dir", "oc"], columns="ctl",
                      values=["delta", "lo", "hi", "p", "p_ev", "p_ctl", "N"])
    w.columns = [f"{a}_{b}" for a, b in w.columns]
    w = w.reset_index()
    w["p_row"] = w[["p_C1", "p_C2"]].max(axis=1)
    w["base"] = w.oc.str.replace(r"(lo|hi)$", "", regex=True)
    w["p_base"] = w.groupby(["claim", "tf", "k", "dir", "base"]).p_row.transform("max")
    w["family"] = w.claim.isin(FAM_CL) & w.oc.isin(ANCHOR) & w.tf.isin([60, 240, 1440])
    ub = w[w.family].drop_duplicates(["claim", "tf", "k", "dir", "base"])[["claim", "tf", "k", "dir", "base", "p_base"]].sort_values("p_base").reset_index(drop=True)
    # строки семейства без данных (N<8 или <5 дней, 1D k=2 A7/A8) остаются в K с p=1 («нет данных → не различимо»): K=216 по протоколу
    full = pd.MultiIndex.from_product([FAM_CL, [60, 240, 1440], [1, 2], sorted(w.dir.unique()), ["x1", "x2", "x3m2"]],
                                      names=["claim", "tf", "k", "dir", "base"]).to_frame(index=False)
    ub = full.merge(ub, how="left", on=["claim", "tf", "k", "dir", "base"])
    print("строк без данных (p=1):", int(ub.p_base.isna().sum()))
    ub["p_base"] = ub.p_base.fillna(1.0)
    ub = ub.sort_values("p_base", kind="stable").reset_index(drop=True)
    K = len(ub)
    print("K семейства", K)
    assert K == 216, K
    ub["p_holm"] = np.maximum.accumulate((K - np.arange(K)) * ub.p_base.values).clip(max=1)
    srt = w.merge(ub[["claim", "tf", "k", "dir", "base", "p_holm"]], on=["claim", "tf", "k", "dir", "base"], how="left")
    srt["p_holm_used"] = srt.p_holm.fillna(srt.p_base)  # вне семейства — сырое p, описательно
    Y = pd.read_csv(ypath)
    sg = []
    for _, r in srt.iterrows():
        y = Y[(Y.claim == r.claim) & (Y.tf == r.tf) & (Y.k == r.k) & (Y.dir == r.dir) & (Y.oc == r.oc) & (Y.N >= 8)]
        yy = y.groupby("year").delta.apply(lambda s: np.sign(s).min() if len(s) else 0)
        sg.append((int((yy > 0).sum()), int((yy < 0).sum()), len(yy)))
    srt["yrs_pos"], srt["yrs_neg"], srt["yrs_n"] = zip(*sg)
    both_pos = (srt.delta_C1 > 0) & (srt.delta_C2 > 0)
    both_neg = (srt.delta_C1 < 0) & (srt.delta_C2 < 0)
    ok_pos = (srt.yrs_neg == 0) & (srt.yrs_n >= 1)
    ok_neg = (srt.yrs_pos == 0) & (srt.yrs_n >= 1)
    srt["verdict"] = np.where((srt.p_holm_used < 0.05) & both_pos & ok_pos & (srt.lo_C1 > 0) & (srt.lo_C2 > 0), "подтверждено",
                     np.where((srt.p_holm_used < 0.05) & both_neg & ok_neg & (srt.hi_C1 < 0) & (srt.hi_C2 < 0), "оспорено", "не различимо"))
    srt["verdict_base"] = srt.groupby(["claim", "tf", "k", "dir", "base"]).verdict.transform(
        lambda v: v.iloc[0] if (v == v.iloc[0]).all() else "не различимо")
    srt = srt.sort_values(["family", "p_holm_used"], ascending=[False, True])
    srt.to_csv(f"{pre}-layer2.csv", index=False)
    print(len(srt), srt[srt.family].verdict.value_counts().to_dict(), "вне семейства:", srt[~srt.family].verdict.value_counts().to_dict())
