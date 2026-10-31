#!/usr/bin/env python3
"""TK-473 (P-TK473 §3): отбор на О и П. python tk473_select.py cand <p1_dir> <out_dir>  -> cand.csv (п.1-2)
python tk473_select.py winners <p1_dir> <pday_dir> <out_dir>  -> winners.csv (п.3: бутстрап по суткам П, B=2000, seed 473, Холм по K_О)"""
import glob
import sys
from math import erf, sqrt

import numpy as np
import pandas as pd

KEY = ["tf", "ev", "dir", "stop", "take", "par", "be_r", "ot", "otp"]
NMIN, NCOIN, SHARE, B = 5, 10, 0.60, 2000


def rd(d, pat):
    fs = sorted(glob.glob(f"{d}/{pat}"))
    return pd.concat([pd.read_csv(f, dtype={k: str for k in KEY}, keep_default_na=False, na_values=[]) for f in fs], ignore_index=True)


def stats(g):
    """по (конфиг, часть): монет n>=5, доля плюсовых, сводный net на сделку"""
    s = g.groupby(["src"] + KEY + ["part", "sym"], sort=False)[["c", "b"]].sum().reset_index()
    e = s[s.c >= NMIN].assign(pl=lambda x: x.b > 0)
    a = e.groupby(["src"] + KEY + ["part"], sort=False).agg(nc=("sym", "size"), share=("pl", "mean")).reset_index()
    t = s.groupby(["src"] + KEY + ["part"], sort=False)[["c", "b"]].sum().reset_index()
    t["net"] = t.b / t.c
    return a.merge(t, on=["src"] + KEY + ["part"])


def passes(a, part):
    x = a[a.part == part]
    ok = (x.nc >= NCOIN) & (x.share >= SHARE) & (x.net > 0)
    return x[ok][["src"] + KEY]


def main(mode, *a):
    if mode == "cand":
        p1, out = a
        st = stats(rd(p1, "*/*.csv.gz"))
        o = passes(st, "O")
        p = passes(st, "P")
        both = o.merge(p, on=["src"] + KEY)
        print("K_grid", st[["src"] + KEY].drop_duplicates().shape[0], "pass_O", len(o), "pass_P", len(p), "cand(O&P)", len(both))
        both.to_csv(f"{out}/cand.csv", index=False)
        pd.DataFrame({"K_O": [len(o)]}).to_csv(f"{out}/k_o.csv", index=False)
    else:
        p1, pday, out = a
        cand = pd.read_csv(f"{out}/cand.csv", dtype=str, keep_default_na=False, na_values=[])
        ko = int(pd.read_csv(f"{out}/k_o.csv").K_O[0])
        d = rd(pday, "*.pday.csv.gz")
        d = d.merge(cand, on=["src"] + KEY)
        d["cfg"] = d[["src"] + KEY].agg("|".join, axis=1)
        g = d.groupby(["cfg", "day"], sort=False)[["c", "b"]].sum().reset_index()
        rng = np.random.default_rng(473)
        rows = []
        for cfg, x in g.groupby("cfg", sort=False):
            c, b = x.c.values.astype(float), x.b.values
            n = len(c)
            idx = rng.integers(0, n, size=(B, n))
            net = b[idx].sum(1) / np.maximum(c[idx].sum(1), 1)
            m = b.sum() / c.sum()
            # p одностороннее H0: net<=0 — нормальное приближение по SE бутстрапа (разрешение B=2000 не даёт p<1/2001, а Холм по K_О его требует)
            se = max(net.std(ddof=1), 1e-12)
            p = 0.5 * (1 - erf(m / se / sqrt(2)))
            rows.append((cfg, c.sum(), m, np.quantile(net, 0.025), np.quantile(net, 0.975), p))
        r = pd.DataFrame(rows, columns=["cfg", "n_P", "net_P", "lo_P", "hi_P", "p"]).sort_values("p")
        # Холм по K_О
        r["rank"] = np.arange(1, len(r) + 1)
        r["p_holm"] = np.minimum(1, np.maximum.accumulate(r.p.values * (ko - r["rank"].values + 1)))
        r["winner"] = (r.p_holm < 0.05) & (r.lo_P > 0)
        cols = ["src"] + KEY
        sp = r.cfg.str.split("|", expand=True)
        sp.columns = cols
        r = pd.concat([sp, r.drop(columns="cfg")], axis=1)
        r.to_csv(f"{out}/p-check.csv", index=False)
        r[r.winner][cols].to_csv(f"{out}/winners.csv", index=False)
        print("K_O", ko, "cand", len(cand), "winners", int(r.winner.sum()))


if __name__ == "__main__":
    main(*sys.argv[1:])
