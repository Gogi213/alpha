#!/usr/bin/env python3
"""Границы терцилей признаков по сигналам B1 ФОРМИРОВАНИЯ (половина F). Читает ТОЛЬКО столбцы признаков (исходы: net, gross, fund, code, hold, mtm, f_out, u_dec - не открываются).
Пишет types_cuts.json (коммитится ДО анализа исходов)."""
import argparse
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ts_types as TY      # noqa: E402

FEATURE_COLS = ["uni", "half", "g_in", "dir", "byb", "exA", "exB", "hour", "M1", "M3", "M7", "V1d", "V3d", "V7d", "F1", "F3", "F7", "drift7", "sig_t", "gsig", "staleA", "staleB", "rA", "rB",
                "share_drv", "drv", "turnA", "turnB", "fund_day", "rateA_day", "rateB_day", "age", "stale"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tables", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    Z = np.load(a.tables, mmap_mode="r")
    meta = json.load(open(a.tables.replace(".npz", ".json"), encoding="utf-8"))
    idx = {c: i for i, c in enumerate(meta["tr_cols"])}
    tr = Z["tr"]
    cols = {c: np.asarray(tr[:, idx[c]]) for c in FEATURE_COLS}
    m = (cols["uni"] == 0) & (cols["half"] == 0)                          # B1, формирование
    d = TY.derive({c: v[m] for c, v in cols.items()})
    cuts = TY.compute_cuts(d)
    cuts["_meta"] = dict(universe="B1", half="F (сутки 2466..2478)", n_signals=int(m.sum()), rule="2/3 и 1/3 квантили по сигналам; ноль фандинга естественный", stale_q=TY.STALE_Q,
                         min_trades=TY.MIN_TRADES, min_assets=TY.MIN_ASSETS, columns_read=FEATURE_COLS)
    json.dump(cuts, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(cuts, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
