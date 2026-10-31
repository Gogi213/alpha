#!/usr/bin/env python3
"""Гейт TK-461 на реальных ctrl-файлах: быстрый trade_fast пересчитывает ctrl-файл, сравнение побитно (CSV) с лежащим эталоном.
python tk461_gate_ctrl.py <SYM> <tf> [ctrl_dir]   (PYTHONPATH=/data/tk0455:<этот каталог>; пути как в tk461-ctrl.sh)"""
import gzip
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd  # noqa: E402
import tk461_sim as S  # noqa: E402
import tk461_scan as K  # noqa: E402
import tk461_ctrl as C  # noqa: E402
from tk461_fast import trade_fast  # noqa: E402
from sancho_l3_trades import Coin  # noqa: E402

sym, tf = sys.argv[1], int(sys.argv[2])
d = sys.argv[3] if len(sys.argv) > 3 else "/data/tk0461/ctrl"
src, ev, fc = f"/srv/alpha-sda/candles-1m/binance-um/{sym}.csv.gz", f"/data/tk0461/ev/{sym}-{tf}.csv.gz", "/data/tk0455/fund"
S.trade = trade_fast
t = time.time()
e = pd.read_csv(ev)
cd = C.controls(src, tf, e)
cn = Coin(src, tf, 2, fc)
rows = []
for s in cd.to_dict("records"):
    dd = int(s["side"])
    for cell in K.CELLS:
        r = K.one(cn, s, dd, cell)
        base = dict(sym=cn.sym, tf=tf, ev=s["ev"], dir=s["dir"], t0=s["tt0"], side=dd, stop=cell[0], take=cell[2], par=cell[3],
                    evk=s["evk"], cj=s["cj"])
        rows.append(dict(**base, reason=r) if isinstance(r, str) else dict(**base, **r))
new = pd.DataFrame(rows).to_csv(index=False)
ref = gzip.open(f"{d}/{sym}-{tf}.csv.gz", "rt").read()
print(sym, tf, "rows", ref.count("\n"), f"fast {time.time() - t:.1f}s", "IDENTICAL" if new == ref else "DIFF")
sys.exit(0 if new == ref else 1)
