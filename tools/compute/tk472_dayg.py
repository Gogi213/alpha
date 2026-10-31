#!/usr/bin/env python3
"""TK-472 v2: суточные суммы (монета, сутки) только по годным конфигам G. python tk472_dayg.py <src> <in.csv.gz> <good.csv> <out.csv.gz>
good.csv: src,tf,ev,dir,stop,take,par,be_r,ot,otp (нормализация как в tk472_core_agg.py)."""
import sys

import pandas as pd

KEY = ["tf", "ev", "dir", "stop", "take", "par", "be_r", "ot", "otp"]


def main(src, f, good, out):
    gd = pd.read_csv(good, dtype=str, keep_default_na=False, na_values=[])
    gd = gd[gd.src == src].drop(columns="src").assign(_g=1)
    if not len(gd):
        return
    x = pd.read_csv(f, usecols=["sym"] + KEY + ["day", "c", "b"], low_memory=False,
                    dtype={"stop": "category", "take": "category", "ot": "category", "otp": "category", "ev": "category", "dir": "category"})
    x = x[~x.ev.astype(str).str.startswith("C-")]
    for k in ("par", "be_r", "ot", "otp", "stop", "take", "ev", "dir"):
        x[k] = x[k].astype(str).replace("nan", "")
    x["tf"] = x.tf.astype(str)
    x = x.merge(gd, on=KEY, how="left")
    x = x[x._g.notna()]
    if len(x):
        x.groupby(["sym", "day"], sort=False)[["c", "b"]].sum().reset_index().to_csv(out, index=False)


if __name__ == "__main__":
    main(*sys.argv[1:])
