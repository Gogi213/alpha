"""П-42 §13.17: минимальный различимый эффект AUC по числу эпизодов U1 — БЕЗ чтения меток (читаются только univ, side, ok_t60, half).
Использование: python3 sigfeat_mde.py <рабочий каталог> [--indep]"""
import json
import math
import sys

import pandas as pd

ep = pd.read_parquet(f"{sys.argv[1]}/episodes.parquet", columns=["univ", "side", "ok_t60", "half"] + (["indep"] if "--indep" in sys.argv else []))
Z = 2.73  # z_{1−0,05/(2·8)}
out = {}
for side in ("long", "short"):
    d = ep[(ep.univ == "U1") & (ep.side == side)]
    if "--indep" in sys.argv:  # набор чувствительности «независимые подходы» (gap_prev_sig_min > 60 мин или нет предыдущего сигнала)
        d = d[d.indep]
    n = int(d.ok_t60.sum())
    out[side] = {"episodes_u1": int(len(d)), "n_ok_t60": n, "n_A": int(((d.half == "A") & d.ok_t60).sum()), "n_B": int(((d.half == "B") & d.ok_t60).sum()),
                 "mde": {str(p): round((Z + 0.84) * math.sqrt((n + 1) / (12 * p * n * (1 - p) * n)) * math.sqrt(2.0), 4) for p in (0.1, 0.3, 0.5)}}
print(json.dumps(out, ensure_ascii=False))
