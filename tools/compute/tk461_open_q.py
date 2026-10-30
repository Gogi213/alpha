#!/usr/bin/env python3
"""TK-461 §8/§12.2 п.4–5: квантили «открытия» (k БУ/x ОТ5 = q25/q50/q75 mfe_r; M ОТ3 = q50/q75 pause; v ОТ4 = q90/q99 against).
python tk461_open_q.py <каталог open/*.csv.gz> <out.csv>   Ключ: kind,tf,ev,dir,side,stop. Исходов не считает."""
import glob
import os
import sys
import pandas as pd

QS = {"mfe_r": (0.25, 0.5, 0.75), "pause": (0.5, 0.75), "against": (0.9, 0.99)}
d = pd.concat([pd.read_csv(p) for p in sorted(glob.glob(os.path.join(sys.argv[1], "*.csv.gz")))], ignore_index=True)
d["stop"] = d["stop"].fillna("")
out = []
for (kind, tf, ev, dr, side, stop), g in d.groupby(["kind", "tf", "ev", "dir", "side", "stop"]):
    for q in QS[kind]:
        out.append((kind, tf, ev, dr, side, stop, q, float(g.val.quantile(q)), len(g), g.sym.nunique()))
r = pd.DataFrame(out, columns=["kind", "tf", "ev", "dir", "side", "stop", "q", "value", "n", "n_coins"])
r.to_csv(sys.argv[2], index=False)
print(len(r), "rows;", d.groupby("kind").size().to_dict())
