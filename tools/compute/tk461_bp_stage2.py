#!/usr/bin/env python3
"""TK-461 D2b bp (остальные G): bpev/out/bp-g*.csv.gz -> <out_dir>/<SYM>-<tf_min>.csv.gz, tf := tf_min числом (1/5/60/240/1440), строки не меняются.
python tk461_bp_stage2.py <out_dir> <bp-g*.csv.gz ...>"""
import os
import sys
import pandas as pd

out = sys.argv[1]
os.makedirs(out, exist_ok=True)
d = pd.concat([pd.read_csv(p) for p in sys.argv[2:]], ignore_index=True)
d["tf"] = d["tf_min"].astype(int)
d = d.drop(columns=["tf_min"])
n = 0
for (sym, tf), g in d.groupby(["sym", "tf"]):
    g.sort_values("tt0", kind="stable").to_csv(os.path.join(out, f"{sym}-{tf}.csv.gz"), index=False)
    n += 1
print(n, "files;", len(d), "rows")
