#!/usr/bin/env python3
"""TK-461 / P-TK461 §12.2 п.1: колонки ss_bps, ot1_bps к событиям Санчо (A1/A4–A8) для ядра TK-463. Исходов не считает.
python tk461_d2b_cols.py <csv.gz минуты> <tf> <events.csv.gz> <out.csv.gz>
Уровень структуры входа (одна и та же цена для SS и ОТ1): A5 — экстремум свипа ext; A6–A8 — дальний край зоны имбаланса
(в ориентации хай: l[j], границы зоны h[j+2]..l[j]); A1/A4 — пробитый уровень L (подтверждённый свинг k=2). Вход E0 = open
первой минуты после tt0+tf (как tk461_scan). dist = d·(ep − уровень)/|ep|·1e4 > 0, иначе NaN (ячейка пропускается)."""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sancho import bars  # noqa: E402


def level_px(ev, dname, row, a):
    """цена уровня структуры в исходной ориентации либо NaN."""
    if ev == "A5":
        return row["ext"]
    if ev in ("A1", "A4"):
        return row["L"]
    j = int(row["j"])
    if j + 2 >= len(a):
        return np.nan
    return a.l.values[j] if dname == "H" else a.h.values[j]  # A6–A8: край зоны, дальний от входа против свипа


def add_cols(ev, tm, o, tf, a):
    ss = np.full(len(ev), np.nan)
    for i, s in enumerate(ev.to_dict("records")):
        e = int(np.searchsorted(tm, s["tt0"] + tf * 60000))
        if e >= len(tm):
            continue
        ep = float(o[e])
        lv = level_px(s["ev"], s["dir"], s, a)
        v = int(s["side"]) * (ep - lv) / abs(ep) * 1e4
        if np.isfinite(v) and v > 0:
            ss[i] = v
    out = ev.copy()
    out["ss_bps"] = ss
    out["ot1_bps"] = ss
    out["ot1_fallback"] = 0
    return out


if __name__ == "__main__":
    path, tf, evp, outp = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
    d = pd.read_csv(path, usecols=[0, 1])
    tm, o = d.iloc[:, 0].values, d.iloc[:, 1].values.astype(float)
    ev = pd.read_csv(evp)
    out = add_cols(ev, tm, o, tf, bars(path, tf))
    out.to_csv(outp, index=False, compression="gzip")
    print(outp, len(out), int(out.ss_bps.notna().sum()))
