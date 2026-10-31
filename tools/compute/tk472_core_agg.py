#!/usr/bin/env python3
"""TK-472: сводка d2b-out-* для кора монет. Один файл (монета, ТФ) -> два csv.gz без пересчёта ядра (В-213).
python tk472_core_agg.py <src> <in.csv.gz> <out_prefix>
  <out_prefix>.cfg.csv.gz : src,sym,tf,ev,dir,stop,take,par,be_r,ot,otp,year,c,b   (c сделок, b сумма net, bps)
  <out_prefix>.day.csv.gz : src,sym,day,c,b                                         (сумма по всем ячейкам монеты за сутки)
Строки контролей (ev начинается с C-) пропускаются."""
import sys

import pandas as pd

KEY = ["tf", "ev", "dir", "stop", "take", "par", "be_r", "ot", "otp"]


def main(src, f, pre):
    x = pd.read_csv(f, usecols=["sym"] + KEY + ["day", "c", "b"], low_memory=False,
                    dtype={"stop": "category", "take": "category", "ot": "category", "otp": "category", "ev": "category", "dir": "category"})
    x = x[~x.ev.astype(str).str.startswith("C-")]
    if not len(x):
        return
    x["year"] = pd.to_datetime(x.day.astype("int64") * 86400000, unit="ms").dt.year
    x["src"] = src
    for k in ("par", "be_r", "ot", "otp", "stop", "take", "ev", "dir"):
        x[k] = x[k].astype(str).replace("nan", "")
    x.groupby(["src", "sym"] + KEY + ["year"], sort=False)[["c", "b"]].sum().reset_index().to_csv(pre + ".cfg.csv.gz", index=False)
    x.groupby(["src", "sym", "day"], sort=False)[["c", "b"]].sum().reset_index().to_csv(pre + ".day.csv.gz", index=False)


if __name__ == "__main__":
    main(*sys.argv[1:])
