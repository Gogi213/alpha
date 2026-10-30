#!/usr/bin/env python3
"""TK-462 шаг 1 (P-TK462 §3 п.1): точный учёт net = gross - комиссии - фандинг по объектам и годам, ставки В-63.
python tk462_step1.py <entries.csv.gz O1/O2> <o3_dir> <out.csv>
Сделки без фандинга (нет месяца у биржи) исключаются из net (n_nofund в строке). Интервал: бутстрап по монетам, B=4000, seed 462.
Статьи со знаком «вклад в среднее net»: gross, -комиссия мейкер, -комиссия тейкер, -фандинг; сумма = net."""
import glob, sys
import numpy as np, pandas as pd

MK, TK, B = 1.26, 3.15, 4000
ent, o3d, out = sys.argv[1:4]
a = pd.read_csv(ent, usecols=["sym", "form", "year", "gross", "n_mk", "fund"])
a["obj"] = np.where(a.form.astype(str).str.upper().str.startswith("B"), "O2", "O1")
a["coin"] = a.sym; a["fee_mk"] = a.n_mk * MK; a["fee_tk"] = (2 - a.n_mk) * TK
parts = []
for p in sorted(glob.glob(o3d + "/*.csv.gz")):
    d = pd.read_csv(p, usecols=["coin", "m", "t_in_ms", "gross_bps", "fund_bps"])
    d["year"] = pd.to_datetime(d.t_in_ms, unit="ms", utc=True).dt.year
    d["obj"] = "O3_m" + d.m.astype(str); d["gross"] = d.gross_bps; d["fund"] = d.fund_bps
    d["fee_mk"] = 0.0; d["fee_tk"] = 2 * TK
    parts.append(d[["coin", "obj", "year", "gross", "fee_mk", "fee_tk", "fund"]])
r = pd.concat([a[["coin", "obj", "year", "gross", "fee_mk", "fee_tk", "fund"]]] + parts, ignore_index=True)
r["nofund"] = r.fund.isna()
rf = r[~r.nofund].copy(); rf["net"] = rf.gross - rf.fee_mk - rf.fee_tk - rf.fund
rng = np.random.default_rng(462)
rows = []
def agg(g, label, yr):
    c = g.groupby("coin").agg(n=("net", "size"), gross=("gross", "sum"), mk=("fee_mk", "sum"), tk=("fee_tk", "sum"), fund=("fund", "sum"), net=("net", "sum"))
    k = len(c); n = c.n.values
    w = rng.multinomial(k, np.ones(k) / k, size=B)
    den = w @ n
    row = {"obj": label, "year": yr, "n": int(n.sum()), "coins": k}
    for col in ["gross", "mk", "tk", "fund", "net"]:
        s = c[col].values; m = s.sum() / n.sum()
        bs = (w @ s)[den > 0] / den[den > 0]
        row[col] = round(m, 3); row[col + "_lo"] = round(np.quantile(bs, .025), 3); row[col + "_hi"] = round(np.quantile(bs, .975), 3)
    row["share_fund_of_minus"] = round(-row["fund"] / -row["net"], 3) if row["net"] < 0 else np.nan
    row["n_nofund"] = int(r[(r.obj == label) & (r.year == yr if yr != "all" else True) & r.nofund].shape[0])
    return row
for obj, g in rf.groupby("obj"):
    rows.append(agg(g, obj, "all"))
    for y, gy in g.groupby("year"):
        if len(gy) >= 8 and gy.coin.nunique() >= 5: rows.append(agg(gy, obj, int(y)))
o = pd.DataFrame(rows); o.to_csv(out, index=False)
print(o[["obj", "year", "n", "coins", "gross", "mk", "tk", "fund", "net", "net_lo", "net_hi", "fund_lo", "fund_hi", "n_nofund"]].to_string())
