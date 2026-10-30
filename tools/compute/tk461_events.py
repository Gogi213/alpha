#!/usr/bin/env python3
"""TK-461 / P-TK461 §3, §9: события Санчо (A1/A4–A8, реальные, без контролей C1/C2) по монете и ТФ из sig_table TK-455 -> csv.gz.
python tk461_events.py <csv.gz минуты> <tf_min> <k> <out.csv.gz>   (PYTHONPATH=/data/tk0455)
Колонки sig_table как есть + sym, tf, side (ожидаемая сторона, side_of TK-455); ключ события = (sym, tf, ev, dir, tt0)."""
import os
import sys
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sancho_l3_sig import sig_table  # noqa: E402
from sancho_l3_trades import side_of  # noqa: E402

if __name__ == "__main__":
    path, tf, k, out = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
    d = sig_table(path, tf, k, ctl=False)
    if len(d):
        d.insert(0, "sym", os.path.basename(path).split(".")[0])
        d.insert(1, "tf", tf)
        d["side"] = [side_of(e, n) for e, n in zip(d.ev, d.dir)]
    d.to_csv(out, index=False, compression="gzip")
    print(out, len(d))
