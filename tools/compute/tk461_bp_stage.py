#!/usr/bin/env python3
"""TK-461 D2b bp: d2b-ev-bp/<SYM>-<метка>.csv.gz (метки H1/H4/D1 и 60/240/1440) -> d2b-ev-bp2/<SYM>-<60|240|1440>.csv.gz (tf числом, строки не меняются).
python tk461_bp_stage.py <in_dir> <out_dir>"""
import glob
import os
import sys
import pandas as pd

M = {"H1": 60, "H4": 240, "D1": 1440, "60": 60, "240": 240, "1440": 1440}
src, out = sys.argv[1:3]
os.makedirs(out, exist_ok=True)
by = {}
for f in sorted(glob.glob(os.path.join(src, "*.csv.gz"))):
    sym, lab = os.path.basename(f)[:-7].rsplit("-", 1)
    by.setdefault((sym, M[lab]), []).append(f)
for (sym, m), fs in by.items():
    d = pd.concat([pd.read_csv(f) for f in fs], ignore_index=True)
    d["tf"] = m
    d.sort_values("tt0", kind="stable").to_csv(os.path.join(out, f"{sym}-{m}.csv.gz"), index=False)
print(len(by), "files")
