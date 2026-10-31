#!/usr/bin/env python3
"""TK-461 возврат Судьи п.2/3: ячейки БУ/ОТ3/ОТ4/ОТ5 (и ОТ+БУ) -- вердикт только на части «проверка» (P-TK461 §8): нечётные монеты x 2024+;
перекрёстные xa (чётные x 2024+), xb (нечётные x <=2023) -- описательно. ОТ1 и базовые ячейки -- по всему периоду (sancho-bp-exits-<дата>.csv).
python tk461_final_part.py <part-dir> <findings-dir> <YYYY-MM-DD>   (part-dir: <fam>-<check|xa|xb>-{cells,years}.csv)"""
import sys
import pandas as pd

pd_, d, dt = sys.argv[1:4]
K = ["tf", "ev", "dir", "stop", "take", "par", "be_r", "ot", "otp"]
fam = {"sancho": "Санчо", "bp": "bp", "s": "S1-S5"}


def rd(f, part, kind):
    x = pd.read_csv(f"{pd_}/{f}-{part}-{kind}.csv")
    for k in ("par", "be_r", "ot", "otp"):
        x[k] = x[k].astype(str).replace("nan", "")
    x["family"] = fam[f]
    return x


rows = []
for f in fam:
    c = rd(f, "check", "cells")
    y = rd(f, "check", "years")
    yy = y[y.n >= 8].groupby(["family"] + K).delta.min().rename("ymin").reset_index()
    c = c.merge(yy, on=["family"] + K, how="left")
    for p in ("xa", "xb"):
        cx = rd(f, p, "cells")[["family"] + K + ["n", "delta"]].rename(columns={"n": f"n_{p}", "delta": f"delta_{p}"})
        c = c.merge(cx, on=["family"] + K, how="left")
    rows.append(c)
c = pd.concat(rows, ignore_index=True)
c["bu_ot"] = (c.be_r != "") | c.ot.isin(["OT3", "OT4", "OT5"])
c = c[c.bu_ot]
c["h_pos"] = (c.p_holm < 0.05) & (c.delta > 0)
c["h_pos_yr"] = c.h_pos & (c.ymin > 0)
c["h_neg"] = (c.p_holm < 0.05) & (c.delta < 0)
c.to_csv(f"{d}/sancho-bp-exits-check-{dt}.csv", index=False, float_format="%.4f")
g = c.groupby("family")
t = pd.DataFrame({"ячеек БУ/ОТ3-5": g.size(), "Холм Δ>0": g.h_pos.sum(), "…и все годы": g.h_pos_yr.sum(), "Холм Δ<0": g.h_neg.sum(),
                  "…и перекрёстные xa,xb Δ>0": g.apply(lambda x: (x.h_pos_yr & (x.delta_xa > 0) & (x.delta_xb > 0)).sum())}).astype(int)
t.loc["всего"] = t.sum()
top = c[c.h_pos_yr].sort_values("base", ascending=False).head(10)
print(t.to_markdown())
print(top[["family", "tf", "ev", "dir", "side", "stop", "take", "be_r", "ot", "otp", "n", "syms", "base", "delta", "lo", "hi", "ymin", "delta_xa", "delta_xb"]].round(2).to_markdown(index=False))
