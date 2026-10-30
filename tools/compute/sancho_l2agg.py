#!/usr/bin/env python3
"""TK-451 / P-TK451 §5: агрегатор слоя 2. Δ = p_событие − p_контроль (C1, C2), бутстреп по дням UTC (B=4000, seed 451), Холм.
python tools/compute/sancho_l2agg.py <каталог l2> <out-prefix> [NX]
Исходы X1/X2 (X3 «ход m·ATR» слоем 2 не считался — отклонение от §10 п.6, K=144 вместо 216, пишется в отчёт)."""
import glob
import os
import sys
from multiprocessing import Pool
import numpy as np
import pandas as pd

CLAIMS = {  # claim: (фильтр, t0-суффикс)
    "A1": (lambda d: d.brk == 1, "a0"),
    "A2": (lambda d: (d.brk == 1) & (d.depth < 0.2), "a0"),
    "A3": (lambda d: (d.brk == 1) & (d.depth >= 0.2) & (d.depth < 0.5), "a0"),
    "A4": (lambda d: (d.brk == 1) & (d.depth > 0.5), "a0"),
    "A5": (lambda d: d.brk == 0, "a0"),
    "A6": (lambda d: (d.brk == 0) & (d.imb == 1), "a2"),
    "A7": (lambda d: (d.brk == 0) & (d.bos == 1), "a1"),
    "A8": (lambda d: (d.brk == 0) & (d.imb == 1) & (d.bos == 1), "a2"),
}
B = 4000


def one(path):
    b = os.path.basename(path)[3:-7].split("-")  # SYM-tf-k
    sym, tf, k = b[0], int(b[1]), int(b[2])
    d = pd.read_csv(path)
    d["day"] = d.t // 86400000
    out = []
    for cl, (flt, sf) in CLAIMS.items():
        m = flt(d) & (d[f"u_{sf}"] == 1)
        for oc in ("x1", "x2"):
            x = d[f"{oc}_{sf}"]
            s = d[m & (x >= 0)].assign(v=x[m & (x >= 0)])
            if s.empty:
                continue
            real = s[s.role == "real"][["ev", "dir", "day", "v"]].rename(columns={"v": "vr"})
            for ctl in ("C1", "C2"):
                c = s[s.role == ctl].groupby(["ev", "dir"], as_index=False).v.mean().rename(columns={"v": "vc"})
                j = real.merge(c, on=["ev", "dir"])
                nr = len(real)
                if j.empty:
                    g = pd.DataFrame(columns=["dir", "day", "n", "sr", "sc"])
                else:
                    g = j.groupby(["dir", "day"]).agg(n=("vr", "size"), sr=("vr", "sum"), sc=("vc", "sum")).reset_index()
                g["real_n_all"] = nr
                g["claim"], g["oc"], g["ctl"], g["sym"], g["tf"], g["k"] = cl, oc, ctl, sym, tf, k
                out.append(g)
    return pd.concat(out) if out else pd.DataFrame()


def boot(g):
    """g: по дням (day, n, sr, sc) → Δ, ci, p (бутстреп по дням)."""
    g = g.groupby("day")[["n", "sr", "sc"]].sum()
    n, sr, sc = g.n.values.astype(float), g.sr.values, g.sc.values
    N = n.sum()
    if N < 8 or len(g) < 5:
        return None
    d0 = (sr.sum() - sc.sum()) / N
    rng = np.random.default_rng(451)
    cnt = rng.multinomial(len(g), np.full(len(g), 1 / len(g)), B).astype(float)
    nn = cnt @ n
    dd = (cnt @ sr - cnt @ sc) / np.where(nn > 0, nn, np.nan)
    p = 2 * min((dd <= 0).mean(), (dd >= 0).mean()) + 1 / B
    return dict(N=int(N), days=len(g), p_ev=sr.sum() / N, p_ctl=sc.sum() / N, delta=d0,
                lo=np.nanpercentile(dd, 2.5), hi=np.nanpercentile(dd, 97.5), p=min(p, 1.0))


if __name__ == "__main__":
    src, pre = sys.argv[1], sys.argv[2]
    nx = int(sys.argv[3]) if len(sys.argv) > 3 else 15
    fs = sorted(glob.glob(f"{src}/l2-*.csv.gz"))
    if os.environ.get("ONLY"):
        fs = [f for f in fs if os.environ["ONLY"] in f]
    with Pool(nx) as p:
        parts = [x for x in p.map(one, fs, chunksize=1) if len(x)]
    A = pd.concat(parts, ignore_index=True)
    A["year"] = pd.to_datetime(A.day * 86400, unit="s").dt.year
    A.to_csv(f"{pre}-days.csv.gz", index=False, compression="gzip")
    rows, yrs, syms = [], [], []
    for (cl, oc, ctl, tf, k, dr), g in A.groupby(["claim", "oc", "ctl", "tf", "k", "dir"]):
        r = boot(g)
        if r:
            rows.append(dict(claim=cl, oc=oc, ctl=ctl, tf=tf, k=k, dir=dr, nsym=g.sym.nunique(),
                             real_n=int(g.groupby("sym").real_n_all.first().sum()), **r))
        for y, gy in g.groupby("year"):
            ry = boot(gy)
            nsy = gy.sym.nunique()
            if ry and nsy >= 5:
                yrs.append(dict(claim=cl, oc=oc, ctl=ctl, tf=tf, k=k, dir=dr, year=y, nsym=nsy, **ry))
    R = pd.DataFrame(rows)
    # строка семейства = (claim, tf, k, dir, oc); p строки = max(p_C1, p_C2); Холм по K строк
    w = R.pivot_table(index=["claim", "tf", "k", "dir", "oc"], columns="ctl", values=["delta", "lo", "hi", "p", "p_ev", "p_ctl", "N"])
    w.columns = [f"{a}_{b}" for a, b in w.columns]
    w = w.reset_index()
    w["p_row"] = w[["p_C1", "p_C2"]].max(axis=1)
    o = w.p_row.rank(method="first").astype(int)
    K = len(w)
    srt = w.sort_values("p_row").reset_index(drop=True)
    adj = np.maximum.accumulate((K - np.arange(K)) * srt.p_row.values).clip(max=1)
    srt["p_holm"] = adj
    Y = pd.DataFrame(yrs, columns=["claim", "oc", "ctl", "tf", "k", "dir", "year", "nsym", "N", "days", "p_ev", "p_ctl", "delta", "lo", "hi", "p"])
    sg = []
    for _, r in srt.iterrows():
        y = Y[(Y.claim == r.claim) & (Y.tf == r.tf) & (Y.k == r.k) & (Y.dir == r.dir) & (Y.oc == r.oc) & (Y.N >= 8)]
        yy = y.groupby("year").delta.apply(lambda s: np.sign(s).min() if len(s) else 0)  # знак по обоим контролям
        sg.append((int((yy > 0).sum()), int((yy < 0).sum()), len(yy)))
    srt["yrs_pos"], srt["yrs_neg"], srt["yrs_n"] = zip(*sg)
    both_pos = (srt.delta_C1 > 0) & (srt.delta_C2 > 0)
    both_neg = (srt.delta_C1 < 0) & (srt.delta_C2 < 0)
    ok_y_pos = (srt.yrs_neg == 0) & (srt.yrs_n >= 1)
    ok_y_neg = (srt.yrs_pos == 0) & (srt.yrs_n >= 1)
    srt["verdict"] = np.where((srt.p_holm < 0.05) & both_pos & ok_y_pos & (srt.lo_C1 > 0) & (srt.lo_C2 > 0), "подтверждено",
                     np.where((srt.p_holm < 0.05) & both_neg & ok_y_neg & (srt.hi_C1 < 0) & (srt.hi_C2 < 0), "оспорено", "не различимо"))
    srt.to_csv(f"{pre}-layer2.csv", index=False)
    Y.to_csv(f"{pre}-layer2-years.csv", index=False)
    sy = A.groupby(["claim", "oc", "ctl", "tf", "k", "sym"])[["n", "sr", "sc"]].sum().reset_index()
    sy.to_csv(f"{pre}-layer2-syms.csv", index=False)
    print(len(srt), srt.verdict.value_counts().to_dict())
