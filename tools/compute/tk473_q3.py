#!/usr/bin/env python3
"""TK-473 Q-3: победители holdout 2026 при худшем (все ноги тейкер, bt) и лучшем (все мейкер, bm) конце тарифа на H (день >= 20454).
python tk473_q3.py <q3-out> <winners.csv(-winners.csv holdout: cfg,n_H,net_H,lo_H)> <out.csv>
net = sum(b|bt|bm)/sum(c) по суткам H; интервал — бутстрап по суткам B=2000 (seed 4731), как в tk473_holdout."""
import glob, sys
import numpy as np
import pandas as pd

KEY = ["tf", "ev", "dir", "stop", "take", "par", "be_r", "ot", "otp"]
D2026 = 20454
B = 2000


def main(src, wf, out):
    xs = []
    for f in sorted(glob.glob(f"{src}/*.csv.gz")):
        try:
            x = pd.read_csv(f, low_memory=False, usecols=["sym", "day"] + KEY + ["c", "b", "bt", "bm"])
        except pd.errors.EmptyDataError:
            continue
        xs.append(x[x.day >= D2026])
    x = pd.concat(xs, ignore_index=True)
    for k in KEY:
        x[k] = x[k].astype(str).replace("nan", "")
    # ключ как в winners: par/be_r/otp как у pass-скрипта (строка из float)
    x["cfg"] = x[KEY].agg("|".join, axis=1)
    w = pd.read_csv(wf, dtype=str, keep_default_na=False)
    rng = np.random.default_rng(4731)
    rows = []
    for cfg, g in x.groupby("cfg"):
        d = g.groupby("day")[["c", "b", "bt", "bm"]].sum()
        c = d.c.values.astype(float)
        r = dict(cfg=cfg, n_q3=int(c.sum()), days_q3=len(d), last_day=int(d.index.max()))
        for nm, col in (("base", "b"), ("worst", "bt"), ("best", "bm")):
            v = d[col].values
            idx = rng.integers(0, len(d), size=(B, len(d)))
            bs = v[idx].sum(1) / np.maximum(c[idx].sum(1), 1)
            r[f"net_{nm}"] = v.sum() / c.sum()
            r[f"lo_{nm}"] = np.quantile(bs, .025)
            r[f"hi_{nm}"] = np.quantile(bs, .975)
        rows.append(r)
    r = pd.DataFrame(rows)
    r["q3_pos"] = (r.net_worst > 0) & (r.lo_worst > 0)
    r["q3_sign"] = r.net_worst > 0
    r.to_csv(out, index=False)
    print(r.assign(cfg=r.cfg.str[-34:]).round(1).to_string())
    print("winners in csv", len(w), "q3 rows", len(r), "sign", int(r.q3_sign.sum()), "strict", int(r.q3_pos.sum()))


if __name__ == "__main__":
    main(*sys.argv[1:])
