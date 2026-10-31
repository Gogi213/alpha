#!/usr/bin/env python3
"""TK-473 (P-TK473 §4-5): счёт победителей на H (2026-01-01..09-15). python tk473_holdout.py <p2_dir> <winners.csv> <out_prefix>
-> prefix-winners.csv, -months.csv, -coins.csv, -summary.json"""
import glob, json, sys
from math import erf, sqrt
import numpy as np
import pandas as pd

KEY = ["src", "tf", "ev", "dir", "stop", "take", "par", "be_r", "ot", "otp"]
B = 2000


def rd(d, pat):
    fs = sorted(glob.glob(f"{d}/*/{pat}"))
    return pd.concat([pd.read_csv(f, dtype={k: str for k in KEY}, keep_default_na=False, na_values=[]) for f in fs], ignore_index=True)


def boot(c, v, rng):
    """кластер по суткам: net = sum v / sum c"""
    n = len(c)
    idx = rng.integers(0, n, size=(B, n))
    return v[idx].sum(1) / np.maximum(c[idx].sum(1), 1)


def newcombe(p1, n1, p2, n2):
    def w(p, n):
        z = 1.96; d = 1 + z * z / n
        m = (p + z * z / (2 * n)) / d; h = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
        return m - h, m + h
    l1, u1 = w(p1, n1); l2, u2 = w(p2, n2)
    dd = p1 - p2
    return dd - sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2), dd + sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)


def main(p2, wf, out):
    W = pd.read_csv(wf, dtype=str, keep_default_na=False, na_values=[])
    W["cfg"] = W[KEY].agg("|".join, axis=1)
    hk = rd(p2, "*.hkey.csv.gz")
    hk["cfg"] = hk[KEY].agg("|".join, axis=1)
    hk = hk.groupby("cfg")[["c", "b", "d", "k"]].sum()
    hk["net"] = hk.b / hk.c
    win = hk.index.isin(W.cfg)
    base = hk[(~win) & (hk.c >= 30)]
    wn = hk[win]
    p_base = (base.net > 0).mean(); p_win = (wn.net > 0).mean()
    lo, hi = newcombe(p_win, len(wn), p_base, len(base))
    hd = rd(p2, "*.hday.csv.gz"); hd["cfg"] = hd[KEY].agg("|".join, axis=1)
    hs = rd(p2, "*.hsym.csv.gz"); hs["cfg"] = hs[KEY].agg("|".join, axis=1)
    hd = hd[hd.cfg.isin(W.cfg)]; hs = hs[hs.cfg.isin(W.cfg)]
    rng = np.random.default_rng(4730)
    rows = []
    for cfg, x in hd.groupby("cfg"):
        g = x.groupby("day")[["c", "b", "d", "k"]].sum()
        c = g.c.values.astype(float)
        nb = boot(c, g.b.values, rng); dd = boot(c, g.d.values, rng)
        s = hs[hs.cfg == cfg].groupby("sym")[["c", "b"]].sum(); s = s[s.c >= 5]
        net, dnet = g.b.sum() / c.sum(), g.d.sum() / c.sum()
        sd = max(dd.std(ddof=1), 1e-12)
        rows.append(dict(cfg=cfg, n_H=int(c.sum()), net_H=net, lo_H=np.quantile(nb, .025), hi_H=np.quantile(nb, .975),
                         ctrl_H=g.k.sum() / c.sum(), delta_H=dnet, dlo=np.quantile(dd, .025), dhi=np.quantile(dd, .975),
                         p_delta=0.5 * (1 - erf(dnet / sd / sqrt(2))), coins=len(s), coins_pos=int((s.b > 0).sum()),
                         dir=x.dir.iloc[0], days=len(g)))
    r = pd.DataFrame(rows).sort_values("p_delta")
    n = len(r); r["p_holm"] = np.minimum(1, np.maximum.accumulate(r.p_delta.values * (n - np.arange(n))))
    r["keeps_plus"] = (r.net_H > 0) & (r.lo_H > 0)
    r["sign_kept"] = r.net_H > 0
    r["beats_ctrl_holm"] = (r.p_holm < 0.05) & (r.delta_H > 0)
    r.to_csv(out + "-winners.csv", index=False)
    m = hs.groupby(["cfg", "month"])[["c", "b", "d", "k"]].sum().reset_index(); m["net"] = m.b / m.c
    m.to_csv(out + "-months.csv", index=False)
    cn = hs.groupby(["cfg", "sym"])[["c", "b"]].sum().reset_index(); cn["net"] = cn.b / cn.c
    cn.to_csv(out + "-coins.csv", index=False)
    # пул победителей (один набор сделок не независим — описательно)
    mm = hs.groupby("month")[["c", "b", "k"]].sum(); mm["net_mean"] = mm.b / mm.c
    summ = dict(W=n, keeps_plus=int(r.keeps_plus.sum()), sign_kept=int(r.sign_kept.sum()), beats_ctrl=int(r.beats_ctrl_holm.sum()),
                base_n=len(base), base_share_pos=p_base, win_share_pos=p_win, newcombe=[lo, hi],
                base_median_net=float(base.net.median()), base_mean_net=float((base.b.sum() / base.c.sum())),
                win_mean_net=float(wn.b.sum() / wn.c.sum()), ctrl_mean_net=float(wn.k.sum() / wn.c.sum()),
                month_net=mm.net_mean.round(1).to_dict(), month_n=mm.c.to_dict())
    json.dump(summ, open(out + "-summary.json", "w"), indent=1, default=float)
    print(json.dumps(summ, indent=1, default=float))
    print(r[["cfg", "n_H", "net_H", "lo_H", "ctrl_H", "delta_H", "p_holm", "coins", "coins_pos"]].assign(cfg=lambda x: x.cfg.str[-40:]).round(1).to_string())


if __name__ == "__main__":
    main(*sys.argv[1:])
