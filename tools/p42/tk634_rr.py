#!/usr/bin/env python3
"""П-42 §13.34 (TK-634): risk:reward баз h775 / o566 по готовым сделкам — печатается ДО любого поиска (приказ владельца 19.11, `risk-reward-first`).
  python3 tk634_rr.py [--uni h775|o566] [--out <файл.json>]
База вселенной: `r5.Uni.base(окно, сторона)`; сделки — зачётные сутки (`st.splice`-правило), P&L сделки = gross − fees ($, ставка 500 $, плечо 2 → маржа 250 $).
Только чтение; новых сделок не считает."""
import argparse
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk604_stats as st  # noqa: E402
import tk620_rr as rr  # noqa: E402
import tk625_run as r5  # noqa: E402


def base_rows(u, side):
    """Строки сделок базы стороны в зачётных сутках склейки (`kind`, `pnl = gross − fees`)."""
    rows = []
    for w in st.TRADE_WINDOWS:
        t = st.read_trades_csv(os.path.join(u.base(w.name, side), r5.ENG[side] + "-trades.csv"))
        d = st.abs_day(w, t.entry_ms)
        lo_i, hi_i = (w.lod - st.D0).days, (w.hid - st.D0).days
        for i in np.nonzero((d >= lo_i) & (d <= hi_i))[0]:
            rows.append(dict(kind=t.kind[i], pnl=float(t.gross[i] - t.fees[i])))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uni", default="h775")
    ap.add_argument("--out")
    a = ap.parse_args()
    u = r5.Uni(a.uni)
    out = {}
    for side in st.SIDES:
        rows = base_rows(u, side)
        r = rr.rr_stats(rows)
        out[side] = r
        print("\n".join(rr.fmt("%s_base %s" % (side, u.name), r)))
        print("стоп-выход при плече 2 (маржа %.0f $): средний убыток %.2f %% маржи; один стоп = %.1f средних выигрышей\n" % (
            rr.MARGIN, r["stops"]["mean_loss_pct"], abs(r["stops"]["mean_loss"]) / r["mean_win"] if r["mean_win"] else float("nan")))
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1, default=float)


if __name__ == "__main__":
    main()
