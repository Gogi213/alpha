#!/usr/bin/env python3
"""TK-462 шаг 0 (P-TK462 §8): выписка сделок O1/O2 из кэша TK-455 (role=real, якорные ячейки) + пересчёт комиссий на ставки В-63.
python tools/tk462_enum.py <run_dir> <out.csv.gz>
Ноги: fee_mix кэша = 2*n_mk + 5*n_tk при n_mk+n_tk=2 (ставки P455 2/5) -> n_mk=(10-fee_mix)/3; сверка целости — в выводе.
net = gross - fee - fund (fund > 0 — расход, как acc() в sancho_l3_trades). Ставки В-63: мейкер 1,26, тейкер 3,15 bps за ногу."""
import glob, os, sys
import pandas as pd

MK, TK = 1.26, 3.15
run, out = sys.argv[1], sys.argv[2]
parts, bad = [], 0
for p in sorted(glob.glob(os.path.join(run, "*-[0-9]*.csv.gz"))):
    if p.endswith("-agg.csv.gz"):
        continue
    d = pd.read_csv(p, usecols=["sym", "tf", "role", "evi", "ev", "dir", "t0", "side", "form", "E", "T", "N", "reason",
                                "gross", "fee_mix", "fund", "risk", "bars"])
    d = d[(d.role == "real") & d.gross.notna()].copy()
    nm = (10.0 - d.fee_mix) / 3.0
    bad += int(((nm - nm.round()).abs() > 1e-9).sum() + ((nm < 0) | (nm > 2)).sum())
    d["n_mk"] = nm.round().astype("int8")
    d["fee_v3"] = d.n_mk * MK + (2 - d.n_mk) * TK
    d["net_v3"] = d.gross - d.fee_v3 - d.fund
    d["year"] = pd.to_datetime(d.t0, unit="ms", utc=True).dt.year.astype("int16")
    parts.append(d.drop(columns=["role"]))
r = pd.concat(parts, ignore_index=True)
r.to_csv(out, index=False, compression="gzip")
print("rows", len(r), "files", len(parts), "bad_legs", bad)
print(r.groupby(["form", "year"]).agg(n=("gross", "size"), gross=("gross", "mean"), fee=("fee_v3", "mean"),
                                       fund=("fund", "mean"), net=("net_v3", "mean")).round(3).to_string())
