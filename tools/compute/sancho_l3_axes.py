#!/usr/bin/env python3
"""TK-455 / P-TK455 §7: кривые по осям E1/S2/T-R/N и воронка сетки — агрегация без пересчёта (В-213) из <run>/*-agg.csv.gz.
Суммы по монетам и годам на ячейку (ev, dir, tf, role, form, E, W, S, m, T, r, N); средние считает отчёт.
python sancho_l3_axes.py <run_dir> <out.csv>
Второй выход <out без .csv>-overlap.csv: доля якорных сделок, чей сигнал пришёл до конца предыдущей сделки той же (монета, ТФ, ev, dir, форма):
конец = t0 + bars*ТФ мин (приближённо; сделки по сигналам считаются независимо, перекрытия не исключаются)."""
import glob
import os
import sys
import pandas as pd

K = ["ev", "dir", "tf", "role", "form", "E", "W", "S", "m", "T", "r", "N"]
run, out = sys.argv[1], sys.argv[2]
parts = []
for f in sorted(glob.glob(os.path.join(run, "*-agg.csv.gz"))):
    d = pd.read_csv(f)
    d["tf"] = int(os.path.basename(f).split("-")[1])
    d = d[d.role != "C3"]
    for c in ("S", "T"):
        d[c] = d[c].fillna("")
    for c in ("m", "r"):
        d[c] = d[c].fillna(-1.0)
    parts.append(d.drop(columns=["c3", "year"]).groupby(K, as_index=False).sum())
A = pd.concat(parts, ignore_index=True).groupby(K, as_index=False).sum()
A.to_csv(out, index=False)
print(len(parts), "файлов,", len(A), "ячеек")

ov = []
for f in sorted(glob.glob(os.path.join(run, "*.csv.gz"))):
    if f.endswith("-agg.csv.gz") or ".tmp" in f:
        continue
    d = pd.read_csv(f, usecols=["sym", "tf", "role", "ev", "dir", "form", "t0", "gross", "bars"])
    d = d[(d.role == "real") & d.gross.notna()].sort_values("t0")
    d["end"] = d.t0 + d.bars * d.tf * 60000
    g = d.groupby(["ev", "dir", "form"])
    d["prev_end"] = g.end.transform(lambda s: s.cummax().shift(1))
    d["ovl"] = d.t0 < d.prev_end
    ov.append(d.groupby(["ev", "dir", "form", "tf"], as_index=False).agg(trades=("ovl", "size"), overlapped=("ovl", "sum")))
O = pd.concat(ov, ignore_index=True).groupby(["ev", "dir", "form", "tf"], as_index=False).sum()
O.to_csv(out[:-4] + "-overlap.csv", index=False)
print(len(O), "строк перекрытия")
