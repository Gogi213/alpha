#!/usr/bin/env python3
"""TK-472 v2 (P-TK472 §v2, предписание Судьи tk472-return-2026-10-31.md).
python tk472_core2.py stage1 <agg_dir> <out_dir>                           -> out_dir/good.csv, gmeta.json
python tk472_core2.py stage3 <agg_dir> <dayg_dir> <candles_dir> <out_dir>  -> core2.csv, core2.json, literal.csv"""
import glob
import hashlib
import json
import math
import sys

import numpy as np
import pandas as pd

KEY = ["tf", "ev", "dir", "stop", "take", "par", "be_r", "ot", "otp"]
D2024 = 19723
NMIN, NCOIN, GMIN, TOPQ, FCORE, SHARE = 5, 10, 20, 0.01, 0.60, 0.60


def load(agg, kind):
    fs = sorted(glob.glob(f"{agg}/*/*.{kind}.csv.gz"))
    return pd.concat([pd.read_csv(f, low_memory=False, keep_default_na=False, na_values=[]) for f in fs], ignore_index=True)


def wilson(k, n, z=1.96):
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def newcombe(k1, n1, k2, n2):
    l1, u1 = wilson(k1, n1)
    l2, u2 = wilson(k2, n2)
    p1, p2 = k1 / n1, k2 / n2
    return (p1 - p2 - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2), p1 - p2 + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2))


def cfg_tables(agg):
    cfg = load(agg, "cfg")
    for k in KEY:
        cfg[k] = cfg[k].astype(str)
    cfg["win"] = np.where(cfg.year <= 2023, 0, 1)
    g = cfg.groupby(["src"] + KEY + ["sym", "win"], sort=False)[["c", "b"]].sum().reset_index()
    cf = g[["src"] + KEY].drop_duplicates().reset_index(drop=True)
    cf["cid"] = np.arange(len(cf))
    g = g.merge(cf, on=["src"] + KEY)
    syms = sorted(g.sym.unique())
    si = {s: i for i, s in enumerate(syms)}
    n = len(cf)
    C = np.zeros((2, n, len(syms)))
    B = np.zeros((2, n, len(syms)))
    ix = (g.win.values, g.cid.values, g.sym.map(si).values)
    C[ix] = g.c.values
    B[ix] = g.b.values
    return cf, syms, C, B, cfg


def select_good(C, B):
    el = C[0] >= NMIN
    nco = el.sum(1)
    pool = B[0].sum(1) / np.maximum(C[0].sum(1), 1)
    ok = (nco >= NCOIN) & (pool > 0)
    meta = dict(configs=int(len(nco)), with_ncoin=int((nco >= NCOIN).sum()), pooled_pos=int(ok.sum()))
    if ok.sum() >= GMIN:
        good, rule = ok, "pooled>0"
    else:
        cand = np.where(nco >= NCOIN)[0]
        k = max(GMIN, math.ceil(TOPQ * len(cand)))
        top = cand[np.argsort(-pool[cand])[:k]]
        good = np.zeros(len(nco), bool)
        good[top] = True
        rule = f"top {k} of {len(cand)} by pooled net <=2023"
    meta.update(rule=rule, good=int(good.sum()))
    return good, meta


def stage1(agg, out):
    cf, syms, C, B, _ = cfg_tables(agg)
    good, meta = select_good(C, B)
    cf[good].drop(columns="cid").to_csv(f"{out}/good.csv", index=False)
    json.dump(meta, open(f"{out}/gmeta.json", "w"), indent=1)
    print(meta)


def cl(d):
    """d: c,b по суткам (кластеры) -> (net, lo, hi, c)."""
    c, b = d.c.sum(), d.b.sum()
    m = b / c
    se = math.sqrt(((d.b - m * d.c) ** 2).sum()) / c
    return m, m - 1.96 * se, m + 1.96 * se, c


def stage3(agg, dayg, candles, out):
    cf, syms, C, B, cfg = cfg_tables(agg)
    good, meta = select_good(C, B)
    S = len(syms)
    gi = np.where(good)[0]
    # буквальный вопрос: конфиги с долей плюса >= 0.6 на монетах (n>=5) в обоих окнах, >= 10 монет в каждом
    sh, nc, pn = [], [], []
    for w in (0, 1):
        el = C[w] >= NMIN
        pl = el & (B[w] > 0)
        nc.append(el.sum(1))
        sh.append(pl.sum(1) / np.maximum(el.sum(1), 1))
        pn.append(B[w].sum(1) / np.maximum(C[w].sum(1), 1))
    lit = (nc[0] >= NCOIN) & (nc[1] >= NCOIN) & (sh[0] >= SHARE) & (sh[1] >= SHARE)
    L = cf[lit].copy()
    for w, nm in ((0, "sel"), (1, "val")):
        L[f"coins_{nm}"], L[f"share_{nm}"], L[f"net_{nm}"] = nc[w][lit], sh[w][lit], pn[w][lit]
    L["in_good"] = good[lit]
    L.sort_values("share_val", ascending=False).drop(columns="cid").to_csv(f"{out}/literal.csv", index=False)
    # f монеты по годным конфигам и остаток относительно рынка
    R = pd.DataFrame(index=syms)
    for w, nm in ((0, "sel"), (1, "val")):
        el = C[w][gi] >= NMIN
        pl = el & (B[w][gi] > 0)
        m = pl.sum(1) / np.maximum(el.sum(1), 1)  # доля плюса конфига по монетам
        ne = el.sum(0)
        R[f"elig_{nm}"] = ne
        R[f"f_{nm}"] = pl.sum(0) / np.where(ne == 0, np.nan, ne)
        r = np.where(el, pl.astype(float) - m[:, None], 0.0)
        R[f"resid_{nm}"] = r.sum(0) / np.where(ne == 0, np.nan, ne)
    # суточные суммы по G -> интервалы
    day = pd.concat([pd.read_csv(f) for f in sorted(glob.glob(f"{dayg}/*/*.csv.gz"))], ignore_index=True)
    day = day.groupby(["sym", "day"], sort=False)[["c", "b"]].sum().reset_index()
    day["win"] = (day.day >= D2024).astype(int)
    chk = 0.0
    for w, nm in ((0, "sel"), (1, "val")):
        d = day[day.win == w]
        for s, x in d.groupby("sym"):
            R.loc[s, [f"net_{nm}", f"lo_{nm}", f"hi_{nm}", f"c_{nm}"]] = list(cl(x))
        cs = C[w][gi].sum(0)
        cc = R[f"c_{nm}"].fillna(0).values
        chk = max(chk, float(np.max(np.abs(cs - cc) / np.maximum(cs, 1))))
    R["even"] = [int(hashlib.md5(x.encode()).hexdigest(), 16) % 2 == 0 for x in syms]
    R["core"] = (R.f_sel >= FCORE) & (R.lo_sel > 0)
    # описательно: свечи
    feat = []
    for x in syms:
        try:
            k = pd.read_csv(f"{candles}/{x}.csv.gz", usecols=["t_open_ms", "h", "l", "c", "quote_v"])
            k["d"] = k.t_open_ms // 86400000
            dd = k.groupby("d").agg(q=("quote_v", "sum"), h=("h", "max"), l=("l", "min"), c=("c", "last"))
            feat.append((dd.index.min(), float(dd.q.median()), float(np.median(1e4 * (dd.h - dd.l) / dd.c))))
        except Exception:
            feat.append((np.nan,) * 3)
    R[["first_day", "med_qvol_day", "med_range_bps"]] = feat
    yy = cfg.groupby(["sym", "year"])[["c", "b"]].sum()
    for y, v in (yy.b / yy.c).unstack("year").items():
        R[f"netall_{y}"] = v.reindex(syms)
    R.index.name = "sym"
    R.reset_index().to_csv(f"{out}/core2.csv", index=False)

    def setnet(mask, w):
        d = day[(day.win == w) & day.sym.isin(R.index[mask])].groupby("day")[["c", "b"]].sum().reset_index()
        return dict(zip(("net", "lo", "hi", "c"), map(float, cl(d)))) if len(d) else None

    def verdict(even=None):
        msk = np.ones(S, bool) if even is None else (R.even.values == even)
        Rv = R[msk & R.f_val.notna().values]
        core, non = Rv[Rv.core], Rv[~Rv.core]
        o = dict(coins=int(msk.sum()), core_n=int((R.core.values & msk).sum()), core_with_val=len(core), non_with_val=len(non))
        if len(core) and len(non):
            k1, n1, k2, n2 = int((core.f_val > .5).sum()), len(core), int((non.f_val > .5).sum()), len(non)
            rng = np.random.default_rng(472)
            allv = Rv.f_val.values
            perm = np.array([float((rng.choice(allv, len(core), replace=False) > .5).mean()) for _ in range(1000)])
            cm = R.core.values & msk
            o.update(core_share_f_gt_half=k1 / n1, non_share_f_gt_half=k2 / n2, diff_ci=list(newcombe(k1, n1, k2, n2)),
                     perm_p95=float(np.percentile(perm, 95)), perm_p=float((1 + (perm >= k1 / n1).sum()) / 1001),
                     core_median_f_val=float(core.f_val.median()), core_net_val=setnet(cm, 1), non_net_val=setnet(~cm & msk, 1),
                     core_net_sel=setnet(cm, 0))
        return o

    res = dict(good=meta, day_check_max_rel_diff=chk)
    res["all"], res["even_open_xa"], res["odd_xb_proverka"] = verdict(), verdict(True), verdict(False)
    ok = (R.resid_sel.notna() & R.resid_val.notna()).values
    a, b = R.resid_sel[ok].rank().values, R.resid_val[ok].rank().values
    rho = float(np.corrcoef(a, b)[0, 1])
    rng = np.random.default_rng(473)
    pm = np.array([np.corrcoef(a, rng.permutation(b))[0, 1] for _ in range(2000)])
    res["resid_rank_corr"] = dict(n=int(ok.sum()), rho=rho, p=float((1 + (pm >= rho).sum()) / 2001))
    res["literal"] = dict(configs_total=int(len(cf)), both_ge_060=int(lit.sum()), in_good=int(L.in_good.sum()))
    json.dump(res, open(f"{out}/core2.json", "w"), indent=1, default=float)
    print(json.dumps(res, default=float)[:3000])


if __name__ == "__main__":
    {"stage1": lambda a: stage1(*a), "stage3": lambda a: stage3(*a)}[sys.argv[1]](sys.argv[2:])
