#!/usr/bin/env python3
"""TK-463 замер: чистое время счёта (без загрузки свечей) эталон tk461_sim.trade и trade_fast на первых N событиях пары, 28 ячеек D2a.
python tk463_bench2.py <минуты.csv.gz> <tf> <events.csv.gz> <funding_cache> [N=100]  -> одна строка: ref_s fast_s rows"""
import os
import sys
import time
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk461_sim as S  # noqa: E402
import tk461_scan as K  # noqa: E402
from tk461_fast import trade_fast  # noqa: E402
from sancho_l3_trades import Coin  # noqa: E402

p, tf, evp, fc = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
n = int(sys.argv[5]) if len(sys.argv) > 5 else 100
cn = Coin(p, tf, 2, fc)
ev = pd.read_csv(evp).head(n).to_dict("records")
S0 = S.trade
out = []
for fn in (S0, trade_fast):
    S.trade = fn
    t = time.perf_counter()
    rows = sum(1 for s in ev for cell in K.CELLS if K.one(cn, s, int(s["side"]), cell) is not None)
    out.append(time.perf_counter() - t)
S.trade = S0
print(f"{out[0]:.3f} {out[1]:.3f} {len(ev) * len(K.CELLS)}")
