"""TK-588 review: independent recompute from /data/p42-t588/episodes.parquet (does not import sigfeat_*).

Usage: python3 review588_stats.py <episodes.parquet> <cache_dir> <out.json>
"""
import json
import sys

import numpy as np
import pandas as pd
from scipy.stats import rankdata, norm

EP, CACHE, OUT = sys.argv[1:4]
NB = 2000
rng = np.random.default_rng(5880)
N_MIN = 106560


def auc(x, y):
    m = ~np.isnan(x)
    x, y = x[m], y[m]
    n1 = int(y.sum()); n0 = len(y) - n1
    if n1 == 0 or n0 == 0:
        return np.nan
    r = rankdata(x)
    return (r[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)


def day_blocks(days, block=4):
    """Moving blocks of `block` consecutive calendar days (circular), same count of days as observed span."""
    ud = np.arange(days.min(), days.max() + 1)
    nd = len(ud)
    idx_by_day = {d: np.nonzero(days == d)[0] for d in ud}
    nblk = int(np.ceil(nd / block))
    out = []
    for _ in range(NB):
        st = rng.integers(0, nd, nblk)
        sel = [idx_by_day[ud[(s + j) % nd]] for s in st for j in range(block)]
        out.append(np.concatenate(sel))
    return out


def coin_clusters(coins):
    uc, inv = np.unique(coins, return_inverse=True)
    groups = [np.nonzero(inv == i)[0] for i in range(len(uc))]
    out = []
    for _ in range(NB):
        pick = rng.integers(0, len(uc), len(uc))
        out.append(np.concatenate([groups[i] for i in pick]))
    return out


def ci(vals):
    v = np.asarray(vals, float)
    v = v[~np.isnan(v)]
    return [float(np.quantile(v, 0.025)), float(np.quantile(v, 0.975))]


def pval(vals, centre):
    v = np.asarray(vals, float)
    v = v[~np.isnan(v)]
    lo, hi = (v <= centre).sum(), (v >= centre).sum()
    return float(min(1.0, (2 * min(lo, hi) + 1) / (len(v) + 1)))


def holm(ps):
    o = np.argsort(ps); m = len(ps); adj = np.empty(m); run = 0
    for r, i in enumerate(o):
        run = max(run, (m - r) * ps[i]); adj[i] = min(1, run)
    return adj.tolist()


def stat_boot(fn, res_d, res_c):
    return [fn(ix) for ix in res_d], [fn(ix) for ix in res_c]


e = pd.read_parquet(EP, filters=[("univ", "==", "U1")])
e = e[e.ok_t60].reset_index(drop=True)
e["y"] = e.o_x1_t60 == 3
e["yf"] = e.o_x1_t60_f01 == 3
e["t"] = (e.sig_ms // 60000).astype(int)

# correct sparseness: share of minutes with zero volume (cache stores no-trade minutes as filled bars, qv = 0)
zv6, zv60, zv1440 = [], [], []
cache = {}
for sym, g in e.groupby("sym"):
    with open(f"{CACHE}/{sym}.b42", "rb") as f:
        f.read(8)
    a = np.fromfile(f"{CACHE}/{sym}.b42", dtype="<i8", offset=8, count=5 * N_MIN).reshape(5, N_MIN)
    o, qv = a[0], a[4]
    first = int(np.nonzero(o)[0][0])
    for i, t in zip(g.index, g.t):
        r = {}
        for w, name in ((360, "zv6h"), (60, "zv60"), (1440, "zv24h")):
            s0 = max(t - w, first)
            r[name] = float((qv[s0:t] == 0).mean()) if t - s0 >= 30 else np.nan
        e.loc[i, ["zv6h", "zv60", "zv24h"]] = [r["zv6h"], r["zv60"], r["zv24h"]]

R = {}
for side in ("long", "short"):
    d = e[e.side == side].reset_index(drop=True)
    days, coins = d.day.to_numpy(), d.sym.to_numpy()
    rd, rc = day_blocks(days), coin_clusters(coins)
    y = d.y.to_numpy()
    out = {"n": len(d), "k": int(y.sum()), "rate": float(y.mean())}
    bd, bc = stat_boot(lambda ix: y[ix].mean(), rd, rc)
    out["rate_ci_day"], out["rate_ci_coin"] = ci(bd), ci(bc)
    # paired O3 - control on the subset with controls
    hc = (d.ctrl_n > 0).to_numpy()
    cy, cc = d.ctrl_o3_x1.to_numpy(), d.ctrl_touch_x1.to_numpy()
    out["ctrl_subset_n"] = int(hc.sum())
    out["rate_on_ctrl_subset"] = float(y[hc].mean())
    out["ctrl_rate"] = float(np.nanmean(cy[hc]))
    out["no_ctrl_rate"] = float(y[~hc].mean())
    f = lambda ix: y[ix][hc[ix]].mean() - np.nanmean(cy[ix][hc[ix]])
    bd, bc = stat_boot(f, rd, rc)
    out["diff"] = float(f(np.arange(len(d))))
    out["diff_ci_day"], out["diff_ci_coin"] = ci(bd), ci(bc)
    out["diff_p"] = max(pval(bd, 0), pval(bc, 0))
    touched = d.o_x1_t60.to_numpy() != 1
    out["o3_given_touch"] = float(y[touched].mean())
    out["ctrl_o3_given_touch"] = float(np.nanmean(cy[hc]) / np.nanmean(cc[hc]))
    # halves
    for h in ("A", "B"):
        m = (d.half == h).to_numpy() & hc
        out[f"diff_{h}"] = float(y[m].mean() - np.nanmean(cy[m]))
    # indep subset
    m = d.indep.to_numpy()
    out["indep"] = {"n": int(m.sum()), "rate": float(y[m].mean()),
                    "diff": float(y[m & hc].mean() - np.nanmean(cy[m & hc]))}
    f = lambda ix: (lambda mm: y[ix][mm].mean() - np.nanmean(cy[ix][mm]))(m[ix] & hc[ix])
    bd, bc = stat_boot(f, rd, rc)
    out["indep"]["diff_ci_day"], out["indep"]["diff_ci_coin"] = ci(bd), ci(bc)
    # concentration
    vc = d[d.y].sym.value_counts()
    out["coins_with_o3"] = int(len(vc)); out["top5_coin_o3"] = int(vc.head(5).sum())
    out["rate_drop_top5coins"] = float(d[~d.sym.isin(vc.head(5).index)].y.mean())
    vcd = d[d.y].day.value_counts()
    out["top5_day_o3"] = int(vcd.head(5).sum())
    out["rate_drop_top5days"] = float(d[~d.day.isin(vcd.head(5).index)].y.mean())
    # clustering: episodes per coin, gap
    out["eps_per_coin_mean"] = float(d.groupby("sym").size().mean())
    out["gap_lt60"] = int((d.gap_prev_sig_min < 60).sum())
    out["o3_among_gap_lt60"] = float(d[d.gap_prev_sig_min < 60].y.mean())
    out["o3_among_gap_lt60_prev_o3"] = None
    # primary AUCs
    feats = ["pierce_ep", "spread_tau", "emp6h", "tpm60", "mv5", "cons60", "c5_btc_7d", "c5_eth_7d"]
    prim = {}
    for fe in feats:
        x = d[fe].to_numpy(float)
        a0 = auc(x, y)
        bd, bc = stat_boot(lambda ix: auc(x[ix], y[ix]), rd, rc)
        hA = (d.half == "A").to_numpy()
        prim[fe] = {"auc": a0, "ci_day": ci(bd), "ci_coin": ci(bc), "p": max(pval(bd, .5), pval(bc, .5)),
                    "aucA": auc(x[hA], y[hA]), "aucB": auc(x[~hA], y[~hA])}
    ph = holm([prim[f]["p"] for f in feats])
    for f_, p_ in zip(feats, ph):
        prim[f_]["p_holm"] = p_
    out["primary"] = prim
    # extra features incl. corrected sparseness
    extra = {}
    for fe in ["zv6h", "zv60", "zv24h", "flat60", "turn24h", "turn1h", "natr_bp", "age_h"]:
        x = d[fe].to_numpy(float)
        bd, bc = stat_boot(lambda ix: auc(x[ix], y[ix]), rd, rc)
        hA = (d.half == "A").to_numpy()
        extra[fe] = {"auc": auc(x, y), "ci_day": ci(bd), "ci_coin": ci(bc), "p": max(pval(bd, .5), pval(bc, .5)),
                     "aucA": auc(x[hA], y[hA]), "aucB": auc(x[~hA], y[~hA]),
                     "auc_f01": auc(x, d.yf.to_numpy()),
                     "share_pos": float((x > 0).mean()), "q": [float(v) for v in np.nanquantile(x, [.5, .8, .9, .99])]}
    out["extra"] = extra
    # corrected Z group: zv6h top quintile (A-half boundary)
    thr = float(np.nanquantile(d[d.half == "A"].zv6h, 0.8))
    Z = (d.zv6h > thr).to_numpy() if thr > 0 else (d.zv6h > 0).to_numpy()
    out["zv6h_Z"] = {"thr": thr, "nZ": int(Z.sum()), "rateZ": float(y[Z].mean()) if Z.any() else None,
                     "rate_out": float(y[~Z].mean()),
                     "ctrlZ": float(np.nanmean(cy[Z & hc])) if (Z & hc).any() else None,
                     "ctrl_out": float(np.nanmean(cy[~Z & hc]))}
    f = lambda ix: y[ix][Z[ix]].mean() - y[ix][~Z[ix]].mean() if Z[ix].any() else np.nan
    bd, bc = stat_boot(f, rd, rc)
    out["zv6h_Z"]["diff_ci_day"], out["zv6h_Z"]["diff_ci_coin"] = ci(bd), ci(bc)
    # themes recomputed
    zone = (d.pierce_ep >= 1).to_numpy()
    th = {"zone_rate": float(y[zone].mean()), "clean_rate": float(y[~zone].mean()), "n_zone": int(zone.sum())}
    for h in ("A", "B"):
        m = (d.half == h).to_numpy()
        th[f"zone_minus_clean_{h}"] = float(y[m & zone].mean() - y[m & ~zone].mean())
    f = lambda ix: y[ix][zone[ix]].mean() - y[ix][~zone[ix]].mean()
    bd, bc = stat_boot(f, rd, rc)
    th["zone_diff_ci_day"], th["zone_diff_ci_coin"] = ci(bd), ci(bc)
    # zone vs clean under deeper-return label and relative to control
    th["zone_rate_f01"], th["clean_rate_f01"] = float(d.yf[zone].mean()), float(d.yf[~zone].mean())
    th["zone_ctrl"], th["clean_ctrl"] = float(np.nanmean(cy[zone & hc])), float(np.nanmean(cy[~zone & hc]))
    c0 = (d.cons60 == 0).to_numpy()
    th["cons0_rate"], th["cons1_rate"] = float(y[c0].mean()), float(y[~c0].mean())
    th["cons0_ctrl"], th["cons1_ctrl"] = float(np.nanmean(cy[c0 & hc])), float(np.nanmean(cy[~c0 & hc]))
    th["cons0_f01"], th["cons1_f01"] = float(d.yf[c0].mean()), float(d.yf[~c0].mean())
    th["cons0_turn24h_med"], th["cons1_turn24h_med"] = float(d.turn24h[c0].median()), float(d.turn24h[~c0].median())
    out["themes"] = th
    # turnover gradient: strict, deep-return, NATR, and control by quintile (A-half boundaries)
    qb = np.nanquantile(d[d.half == "A"].turn24h, [.2, .4, .6, .8])
    qi = np.searchsorted(qb, d.turn24h.to_numpy(), side="left")
    tg = []
    for q in range(5):
        m = qi == q
        tg.append({"q": q + 1, "n": int(m.sum()), "o3": float(y[m].mean()), "o3_f01": float(d.yf[m].mean()),
                   "o3_n1": float((d.o_n1_t60[m] == 3).mean()), "ctrl": float(np.nanmean(cy[m & hc])),
                   "ctrl_touch": float(np.nanmean(cc[m & hc])), "touch": float((d.o_x1_t60[m] != 1).mean()),
                   "tpm60_med": float(d.tpm60[m].median()), "natr_mean": float(d.natr_bp[m].mean()),
                   "zv6h_mean": float(d.zv6h[m].mean())})
    out["turn_grad"] = tg
    # distance reach: trivially reached at signal?
    rr = {}
    for dn, bp in (("0p2", 20), ("0p1", 10), ("0p05", 5)):
        col = d[f"reach_{dn}_s"]
        rr[dn] = {"reached": float(col.notna().mean()), "already_at_signal": float((d.dist_bp <= bp).mean()),
                  "reached_le1s": float((col <= 1).mean()), "mean_s": float(col.mean()),
                  "median_s": float(col.median()),
                  "reached_not_already": float(col[d.dist_bp > bp].notna().mean())}
    rr["dist_bp_q"] = [float(v) for v in d.dist_bp.quantile([.1, .5, .9])]
    out["reach"] = rr
    # MDE with realised share
    n = len(d); n1 = int(y.sum()); n0 = n - n1
    z = norm.ppf(1 - 0.05 / 16)
    out["mde_realised"] = float((z + 0.84) * np.sqrt((n + 1) / (12 * n1 * n0)) * np.sqrt(2))
    # ICC by coin / day (one-way ANOVA)
    def icc(gr):
        g = pd.DataFrame({"y": y.astype(float), "g": gr})
        k = g.groupby("g").y.agg(["mean", "size"])
        N = len(g); a = len(k); m0 = (N - (k["size"] ** 2).sum() / N) / (a - 1)
        msb = (k["size"] * (k["mean"] - g.y.mean()) ** 2).sum() / (a - 1)
        msw = ((g.y - g.g.map(k["mean"])) ** 2).sum() / (N - a)
        rho = (msb - msw) / (msb + (m0 - 1) * msw)
        mbar = N / a
        return float(rho), float(1 + (mbar - 1) * rho)
    out["icc_coin"], out["icc_day"] = icc(coins), icc(days)
    R[side] = out

json.dump(R, open(OUT, "w"), indent=1, default=float)
print("ok")
