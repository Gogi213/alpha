#!/usr/bin/env python3
"""TK-472: кор монет по P-TK472 (правило §3, вердикт §4). Вход: /data/tk0472/agg/<src>/*.{cfg,day}.csv.gz (tk472_core_agg.py).
python tk472_core.py <agg_dir> <candles_dir> <out.csv> <out.json>"""
import glob
import hashlib
import json
import sys

import numpy as np
import pandas as pd

KEY = ["tf", "ev", "dir", "stop", "take", "par", "be_r", "ot", "otp"]
D2024 = 19723
NMIN, FSEL, FSRC, NSRC, MSRC = 5, 0.80, 0.60, 2, 20


def load(agg, kind):
    fs = sorted(glob.glob(f"{agg}/*/*.{kind}.csv.gz"))
    return pd.concat([pd.read_csv(f, low_memory=False, keep_default_na=False, na_values=[]) for f in fs], ignore_index=True)


def wilson(k, n, z=1.96):
    if n == 0:
        return (np.nan, np.nan)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def newcombe(k1, n1, k2, n2):
    l1, u1 = wilson(k1, n1)
    l2, u2 = wilson(k2, n2)
    p1, p2 = k1 / n1, k2 / n2
    return (p1 - p2 - np.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2), p1 - p2 + np.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2))


def main(agg, candles, outc, outj):
    cfg = load(agg, "cfg")
    for k in KEY[3:]:
        cfg[k] = cfg[k].astype(str)
    # bp и bp3: общий ключ ячейки = дубль -> оставить bp3
    kk = ["sym"] + KEY + ["year"]
    b3 = cfg[cfg.src == "bp3"][kk].drop_duplicates()
    bp = cfg[cfg.src == "bp"].merge(b3.assign(_d=1), on=kk, how="left")
    dup = int(bp._d.notna().sum())
    cfg = pd.concat([cfg[cfg.src != "bp"], bp[bp._d.isna()].drop(columns="_d")], ignore_index=True)
    cfg["win"] = np.where(cfg.year <= 2023, "sel", "val")
    g = cfg.groupby(["src", "sym"] + KEY + ["win"], sort=False)[["c", "b"]].sum().reset_index()
    g["elig"] = g.c >= NMIN
    g["plus"] = g.elig & (g.b > 0)
    s = g.groupby(["src", "sym", "win"])[["elig", "plus"]].sum().reset_index()
    s["f"] = s.plus / s.elig.replace(0, np.nan)
    p = s.pivot_table(index="sym", columns=["win", "src"], values=["elig", "plus"], aggfunc="sum")
    syms = sorted(cfg.sym.unique())
    R = pd.DataFrame(index=syms)
    for w in ("sel", "val"):
        for fld in ("elig", "plus"):
            R[f"{fld}_{w}"] = [float(p[fld][w].loc[x].sum()) if x in p.index else 0.0 for x in syms]
        R[f"f_{w}"] = R[f"plus_{w}"] / R[f"elig_{w}"].replace(0, np.nan)
    srcs = sorted(cfg.src.unique())
    for w in ("sel", "val"):
        for sc in srcs:
            e = p["elig"][w][sc].reindex(syms).fillna(0) if sc in p["elig"][w] else pd.Series(0.0, index=syms)
            pl = p["plus"][w][sc].reindex(syms).fillna(0) if sc in p["plus"][w] else pd.Series(0.0, index=syms)
            R[f"f_{sc}_{w}"] = (pl / e.replace(0, np.nan))
            R[f"e_{sc}_{w}"] = e
    # правило 2
    R["nsrc_sel"] = sum(((R[f"f_{sc}_sel"] >= FSRC) & (R[f"e_{sc}_sel"] >= MSRC)).astype(int) for sc in srcs)
    # сводный net и интервал по суткам
    day = load(agg, "day")
    day = day.groupby(["sym", "day"], sort=False)[["c", "b"]].sum().reset_index()
    for w, m in (("sel", day.day < D2024), ("val", day.day >= D2024)):
        d = day[m]
        a = d.groupby("sym")[["c", "b"]].sum()
        mm = a.b / a.c
        r = d.assign(m=d.sym.map(mm))
        r["e2"] = (r.b - r.m * r.c) ** 2
        se = np.sqrt(r.groupby("sym").e2.sum()) / a.c
        R[f"c_{w}"] = a.c.reindex(syms)
        R[f"net_{w}"] = mm.reindex(syms)
        R[f"lo_{w}"] = (mm - 1.96 * se).reindex(syms)
        R[f"hi_{w}"] = (mm + 1.96 * se).reindex(syms)
    yy = cfg.groupby(["sym", "year"])[["c", "b"]].sum()
    yy = (yy.b / yy.c).unstack("year")
    for y in yy.columns:
        R[f"net_{y}"] = yy[y].reindex(syms)
    R["even"] = [int(hashlib.md5(x.encode()).hexdigest(), 16) % 2 == 0 for x in syms]
    R["core"] = (R.f_sel >= FSEL) & (R.nsrc_sel >= NSRC) & (R.lo_sel > 0)
    # описательно: свечи
    feat = []
    for x in syms:
        try:
            k = pd.read_csv(f"{candles}/{x}.csv.gz", usecols=["t_open_ms", "h", "l", "c", "quote_v"])
        except Exception:
            feat.append((np.nan,) * 3)
            continue
        k["d"] = k.t_open_ms // 86400000
        dd = k.groupby("d").agg(q=("quote_v", "sum"), h=("h", "max"), l=("l", "min"), c=("c", "last"))
        feat.append((dd.index.min(), float(dd.q.median()), float(np.median(1e4 * (dd.h - dd.l) / dd.c))))
    R[["first_day", "med_qvol_day", "med_range_bps"]] = feat
    R.index.name = "sym"
    R.reset_index().to_csv(outc, index=False)
    # вердикт §4
    has = R.elig_val >= 20
    Rv = R[has & R.f_val.notna()]
    core, non = Rv[Rv.core], Rv[~Rv.core]
    out = dict(dup_bp_rows=dup, coins=len(R), core_n=int(R.core.sum()), core_with_val=len(core), non_with_val=len(non))
    if len(core) and len(non):
        k1, n1, k2, n2 = int((core.f_val > .5).sum()), len(core), int((non.f_val > .5).sum()), len(non)
        d = newcombe(k1, n1, k2, n2)
        pooled = (core.net_val * core.c_val).sum() / core.c_val.sum()
        rng = np.random.default_rng(472)
        allv = Rv.f_val.values
        perm = [float((rng.choice(allv, len(core), replace=False) > .5).mean()) for _ in range(200)]
        out.update(core_share_f_gt_half=k1 / n1, non_share_f_gt_half=k2 / n2, diff_ci=list(map(float, d)), core_median_f_val=float(core.f_val.median()),
                   core_pooled_net_val=float(pooled), perm_p95=float(np.percentile(perm, 95)),
                   holds=bool(core.f_val.median() >= .6 and pooled > 0 and k1 / n1 >= .75 and d[0] > 0 and k1 / n1 > np.percentile(perm, 95)))
    json.dump(out, open(outj, "w"), indent=1, default=float)
    print(out)


if __name__ == "__main__":
    main(*sys.argv[1:])
