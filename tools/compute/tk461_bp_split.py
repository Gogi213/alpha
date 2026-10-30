#!/usr/bin/env python3
"""TK-461 D2b: колонки bp (bpev/out/bp-g*.csv.gz) -> по файлу на (sym,tf) в формате d2b-ev: <out>/<SYM>-<tf>.csv.gz.
python tk461_bp_split.py <out_dir> <bp-g*.csv.gz ...>   Строки не меняет, исходов не считает."""
import os
import sys
import pandas as pd

out = sys.argv[1]
os.makedirs(out, exist_ok=True)
d = pd.concat([pd.read_csv(p) for p in sys.argv[2:]], ignore_index=True)
n = 0
for (sym, tf), g in d.groupby(["sym", "tf"]):
    g.sort_values("tt0").to_csv(os.path.join(out, f"{sym}-{tf}.csv.gz"), index=False)
    n += 1
print(n, "files;", len(d), "rows")
