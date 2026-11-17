"""П-42 §13.17 (TK-588): проверки признаков, таблицы корзин, разреженность, примеры. Запуск на srv:
python3 sigfeat_stats.py <рабочий каталог> [--boot 2000]
Читает episodes.parquet, trades_ow_e.parquet, signals_all.parquet, crossings.csv.gz, corr/summary.json; пишет results.json, quintiles.csv, auc.csv, grid.csv,
tables.md (рабочие таблицы для отчёта). Основная метка: O3 при X = 1 %, T = 60 мин от сигнала, R = среднее пивотов, U1, rep = 0 (эпизоды).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sigfeat_lib import *  # noqa: F401,F403

PRIMARY = {"чистый/зона": ["pierce_ep", "spread_tau"], "разреженность": ["emp6h", "tpm60"], "резкость подхода": ["mv5", "cons60"],
           "корреляция BTC/ETH": ["c5_btc_7d", "c5_eth_7d"]}
PRIM = [f for v in PRIMARY.values() for f in v]
NUMERIC = ["n_piv", "spread_bp", "spread_tau", "ext_gap_bp", "age_h", "since_born_h", "pierce_ep", "pierce_max_bp", "touch_ep", "since_touch_h",
           "depth_min_bp", "depth_mean_bp", "dist_bp", "since_touch_prior_h", "approach_dur_min",
           "emp2h", "emp6h", "flat60", "tpm15", "tpm60", "gap60_s",
           "mv5", "mv15", "mv60", "maxmv1", "maxrng15", "cons60", "cons120",
           "c1_btc_6h", "c1_eth_6h", "c5_btc_24h", "c5_eth_24h", "c5_btc_7d", "c5_eth_7d", "c15_btc_24h", "c15_eth_24h", "c15_btc_7d", "c15_eth_7d",
           "btc_mv5", "btc_mv15", "eth_mv5", "eth_mv15", "cosig15", "peers7", "peer_sig30",
           "natr_bp", "turn1h", "turn24h"]
EXPLORE = [f for f in NUMERIC if f not in PRIM]
SIDES = ("long", "short")
OUTC = {1: "O1", 2: "O2", 3: "O3", 4: "O4"}
LABEL = "o_x1_t60"


def edges_from(x: np.ndarray):
    x = x[~np.isnan(x)]
    if len(x) == 0:
        return np.array([])
    e = np.unique(np.quantile(x, [0.2, 0.4, 0.6, 0.8]))
    if len(np.unique(x)) < 3:
        e = np.unique(x)[:-1]
    return e


def bucketize(x: np.ndarray, edges: np.ndarray):
    b = np.searchsorted(edges, x, side="left").astype(float)
    b[np.isnan(x)] = np.nan
    return b


def fmt_bucket(edges, i):
    if len(edges) == 0:
        return "все"
    if i == 0:
        return f"≤{edges[0]:.4g}"
    if i == len(edges):
        return f">{edges[-1]:.4g}"
    return f"({edges[i - 1]:.4g}; {edges[i]:.4g}]"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("work")
    ap.add_argument("--boot", type=int, default=2000)
    a = ap.parse_args()
    W = a.work
    ep = pd.read_parquet(f"{W}/episodes.parquet")
    res = {"n_features": {"primary": len(PRIM), "explore": len(EXPLORE)}}
    rng = np.random.default_rng(588)
    md = []
    quint_rows, auc_rows, grid_rows = [], [], []
    U1 = ep[(ep.univ == "U1")]
    # ── сетка метки, исходы, контроль — по сторонам
    for side in SIDES:
        d = U1[(U1.side == side)]
        res[side] = {"episodes_u1": int(len(d)), "episodes_u0": int(((ep.univ == "U0") & (ep.side == side)).sum())}
        for xn in ("0p5", "1", "2"):
            for T in (15, 60, 240):
                col, ok = f"o_x{xn}_t{T}", f"ok_t{T}"
                dd = d[d[ok]]
                cnt = dd[col].value_counts()
                n = len(dd)
                grid_rows.append({"side": side, "ref": "mean", "label": f"X={xn.replace('p', ',')}%", "T": T, "n": n,
                                  **{OUTC[o]: cnt.get(o, 0) / n if n else np.nan for o in OUTC}})
        for kk in (1, 2, 4):
            col = f"o_n{kk}_t60"
            dd = d[d["ok_t60"]]
            cnt = dd[col].value_counts()
            n = len(dd)
            grid_rows.append({"side": side, "ref": "mean", "label": f"X={kk} NATR", "T": 60, "n": n, **{OUTC[o]: cnt.get(o, 0) / n if n else np.nan for o in OUTC}})
        for xn in ("0p5", "1", "2"):
            col = f"ox_x{xn}_t60"
            dd = d[d["ok_t60"]]
            cnt = dd[col].value_counts()
            n = len(dd)
            grid_rows.append({"side": side, "ref": "extreme", "label": f"X={xn.replace('p', ',')}%", "T": 60, "n": n, **{OUTC[o]: cnt.get(o, 0) / n if n else np.nan for o in OUTC}})
    pd.DataFrame(grid_rows).to_csv(f"{W}/grid.csv", index=False)
    # ── основной анализ по сторонам
    for side in SIDES:
        d = U1[(U1.side == side) & U1["ok_t60"]].reset_index(drop=True)
        y = (d[LABEL] == O3).astype(int).to_numpy()
        n = len(d)
        days = d["day"].to_numpy()
        coins = d["sym"].to_numpy()
        half = d["half"].to_numpy()
        r = res[side]
        r["n_label"] = n
        r["rate_o3"] = float(y.mean())
        lo, hi = wilson(int(y.sum()), n)
        r["rate_o3_wilson"] = [lo, hi]
        # интервал доли — блоки по суткам и по монетам
        rs_day = block_resamples(days, a.boot, 4, rng)
        rs_coin = cluster_resamples(coins, a.boot, rng)
        rd = np.array([y[ix].mean() for ix in rs_day])
        rc = np.array([y[ix].mean() for ix in rs_coin])
        r["rate_o3_ci_day"] = [float(np.percentile(rd, 2.5)), float(np.percentile(rd, 97.5))]
        r["rate_o3_ci_coin"] = [float(np.percentile(rc, 2.5)), float(np.percentile(rc, 97.5))]
        for nm, col in (("o3_n2", "o_n2_t60"), ("o3_x2", "o_x2_t60"), ("o3_x0p5", "o_x0p5_t60")):
            r["rate_" + nm] = float((d[col] == O3).mean())
        r["touch_rate"] = float((d[LABEL] != O1).mean())
        r["outcome_shares_x1_t60"] = {OUTC[o]: float((d[LABEL] == o).mean()) for o in OUTC}
        r["control"] = {"o3_x1_mean": float(d["ctrl_o3_x1"].mean()), "o3_n2_mean": float(d["ctrl_o3_n2"].mean()), "touch_x1_mean": float(d["ctrl_touch_x1"].mean()),
                        "ctrl_n_mean": float(d["ctrl_n"].mean()), "episodes_without_control": int((d["ctrl_n"] == 0).sum())}
        r["opposite"] = {f"opp_ge_100bp_t60": float((d["opp_t60_bp"] >= 100).mean()), "opp_ge_100bp_t240": float((d["opp_t240_bp"] >= 100).mean()) if "opp_t240_bp" in d else np.nan}
        # эффективное число
        rho_d, neff_d = icc_binary(y, days)
        rho_c, neff_c = icc_binary(y, coins)
        r["icc"] = {"day": rho_d, "neff_day": neff_d, "coin": rho_c, "neff_coin": neff_c, "n_days": int(len(np.unique(days))), "n_coins": int(len(np.unique(coins))),
                    "episodes_per_coin_max": int(pd.Series(coins).value_counts().max())}
        # MDE (формула §13.17): ΔAUC_min = (z + 0,84)·sqrt((n+1)/(12 n1 n0))·sqrt(DEFF), z = z_{1−0,05/16}
        zz = 2.73
        r["mde_auc"] = {str(p): float((zz + 0.84) * np.sqrt((n + 1) / (12 * (p * n) * ((1 - p) * n))) * np.sqrt(2.0)) for p in (0.1, 0.3, 0.5)}
        # ── AUC по признакам
        A = np.flatnonzero(half == "A")
        B = np.flatnonzero(half == "B")
        rs_dayA = block_resamples(days[A], a.boot // 2, 4, rng)
        rs_dayB = block_resamples(days[B], a.boot // 2, 4, rng)
        feats = PRIM + EXPLORE
        tab = []
        for f in feats:
            x = d[f].to_numpy().astype(float)
            ok = ~np.isnan(x)
            au = auc(x, y)
            vd = boot_auc(x, y, rs_day)
            vc = boot_auc(x, y, rs_coin)
            pd_, pc_ = boot_p(vd), boot_p(vc)
            aA = auc(x[A], y[A])
            aB = auc(x[B], y[B])
            vA = boot_auc(x[A], y[A], rs_dayA)
            vB = boot_auc(x[B], y[B], rs_dayB)
            tab.append({"side": side, "feature": f, "group": "primary" if f in PRIM else "explore", "n_valid": int(ok.sum()), "auc": au,
                        "ci_day": [float(np.percentile(vd, 2.5)), float(np.percentile(vd, 97.5))] if len(vd) else [np.nan, np.nan],
                        "ci_coin": [float(np.percentile(vc, 2.5)), float(np.percentile(vc, 97.5))] if len(vc) else [np.nan, np.nan],
                        "p_day": pd_, "p_coin": pc_, "p": max(pd_, pc_),
                        "auc_A": aA, "auc_B": aB,
                        "ci_A": [float(np.percentile(vA, 2.5)), float(np.percentile(vA, 97.5))] if len(vA) else [np.nan, np.nan],
                        "ci_B": [float(np.percentile(vB, 2.5)), float(np.percentile(vB, 97.5))] if len(vB) else [np.nan, np.nan]})
        t = pd.DataFrame(tab)
        for grp in ("primary", "explore"):
            idx = t.index[t.group == grp]
            adj = holm(list(t.loc[idx, "p"]))
            t.loc[idx, "p_holm"] = adj
        t["sign_same_AB"] = (np.sign(t["auc_A"] - 0.5) == np.sign(t["auc_B"] - 0.5)) & (t["auc_A"].notna() & t["auc_B"].notna())
        t["confirmed"] = (t["group"] == "primary") & (t["p_holm"] < 0.05) & t["sign_same_AB"]
        auc_rows.append(t)
        # ── корзины (границы по A)
        for f in feats:
            x = d[f].to_numpy().astype(float)
            e = edges_from(x[A])
            bk = bucketize(x, e)
            for hh, sel in (("A", A), ("B", B), ("ALL", np.arange(n))):
                for bi in np.unique(bk[sel][~np.isnan(bk[sel])]):
                    s_ = sel[bk[sel] == bi]
                    k_ = int(y[s_].sum())
                    n_ = len(s_)
                    lo_, hi_ = wilson(k_, n_)
                    sh = {OUTC[o]: float((d[LABEL].to_numpy()[s_] == o).mean()) for o in OUTC}
                    cm = d["ctrl_o3_x1"].to_numpy()[s_]
                    ctrl = float(np.nanmean(cm)) if np.any(~np.isnan(cm)) else np.nan
                    quint_rows.append({"side": side, "feature": f, "half": hh, "bucket": fmt_bucket(e, int(bi)), "bi": int(bi), "n": n_, "k_o3": k_, "rate": k_ / n_, "ci_lo": lo_, "ci_hi": hi_,
                                       **sh, "ctrl_o3": ctrl, "excess": k_ / n_ - ctrl if ctrl == ctrl else np.nan, "nan_in_feature": int(np.isnan(x[sel]).sum()) if bi == 0 else 0})
            nanm = np.isnan(x)
            if nanm.any():
                s_ = np.flatnonzero(nanm)
                quint_rows.append({"side": side, "feature": f, "half": "ALL", "bucket": "нет значения", "bi": -1, "n": len(s_), "k_o3": int(y[s_].sum()), "rate": float(y[s_].mean()), "ci_lo": np.nan, "ci_hi": np.nan})
        # ── зона / чистый уровень, флаги вселенных
        z = {}
        for nm, msk in (("clean_pierce0", d["pierce_ep"] == 0), ("zone_pierce_ge1", d["pierce_ep"] >= 1), ("in_pk", d["in_pk"]), ("not_in_pk", ~d["in_pk"]),
                        ("in_ow", d["in_ow"]), ("not_in_ow", ~d["in_ow"])):
            k_, n_ = int(y[msk.to_numpy()].sum()), int(msk.sum())
            z[nm] = {"n": n_, "k": k_, "rate": k_ / n_ if n_ else np.nan, "wilson": wilson(k_, n_),
                     "outcomes": {OUTC[o]: float((d[LABEL][msk] == o).mean()) if n_ else np.nan for o in OUTC},
                     "ctrl": float(d["ctrl_o3_x1"][msk].mean()) if n_ else np.nan}
        r["zone_clean"] = z
        # ── разреженность: Z = верхний квинтиль emp6h (граница по A)
        r["sparse"] = {}
        for univ in ("U1", "U0"):
            du = ep[(ep.univ == univ) & (ep.side == side) & ep["ok_t60"]].reset_index(drop=True)
            yu = (du[LABEL] == O3).astype(int).to_numpy()
            hu = du["half"].to_numpy()
            for nm, col, hi_side in (("emp6h_top20", "emp6h", True), ("tpm60_bottom20", "tpm60", False)):
                xv = du[col].to_numpy().astype(float)
                xa = xv[hu == "A"]
                xa = xa[~np.isnan(xa)]
                if hi_side:
                    q = float(np.quantile(xa, 0.8))
                    Z = (xv > q) if q > 0 else (xv > 0)
                else:
                    q = float(np.quantile(xa, 0.2))
                    Z = xv <= q
                Z = Z & ~np.isnan(xv)
                valid = ~np.isnan(xv)
                nz, nr = int(Z.sum()), int((valid & ~Z).sum())
                rz, rr = (yu[Z].mean() if nz else np.nan), (yu[valid & ~Z].mean() if nr else np.nan)
                nonbreak_total = int((1 - yu[valid]).sum())
                expl = (rr - rz) * nz / nonbreak_total if nonbreak_total else np.nan
                dd_days = du["day"].to_numpy()
                dd_coins = du["sym"].to_numpy()
                diffs_d, diffs_c = [], []
                for ix in block_resamples(dd_days, a.boot // 2, 4, rng):
                    zz_, vv_ = Z[ix], valid[ix]
                    if zz_.sum() and (vv_ & ~zz_).sum():
                        diffs_d.append(yu[ix][vv_ & ~zz_].mean() - yu[ix][zz_].mean())
                for ix in cluster_resamples(dd_coins, a.boot // 2, rng):
                    zz_, vv_ = Z[ix], valid[ix]
                    if zz_.sum() and (vv_ & ~zz_).sum():
                        diffs_c.append(yu[ix][vv_ & ~zz_].mean() - yu[ix][zz_].mean())
                ctrl_z = float(np.nanmean(du["ctrl_o3_x1"].to_numpy()[Z])) if (univ == "U1" and nz) else np.nan
                ctrl_r = float(np.nanmean(du["ctrl_o3_x1"].to_numpy()[valid & ~Z])) if (univ == "U1" and nr) else np.nan
                r["sparse"][f"{univ}_{nm}"] = {"threshold": q, "n_Z": nz, "n_rest": nr, "rate_Z": float(rz), "rate_rest": float(rr), "diff_rest_minus_Z": float(rr - rz),
                                               "ci_diff_day": [float(np.percentile(diffs_d, 2.5)), float(np.percentile(diffs_d, 97.5))] if diffs_d else None,
                                               "ci_diff_coin": [float(np.percentile(diffs_c, 2.5)), float(np.percentile(diffs_c, 97.5))] if diffs_c else None,
                                               "p_day": boot_p(np.array(diffs_d), 0.0) if diffs_d else None, "p_coin": boot_p(np.array(diffs_c), 0.0) if diffs_c else None,
                                               "explained_share_of_nonbreaks": float(expl), "nonbreaks_total": nonbreak_total, "ctrl_Z": ctrl_z, "ctrl_rest": ctrl_r,
                                               "excess_Z": float(rz - ctrl_z) if ctrl_z == ctrl_z else None, "excess_rest": float(rr - ctrl_r) if ctrl_r == ctrl_r else None}
            # монета против момента
            xc = du["emp_coin"].to_numpy().astype(float)
            xm = du["emp6h"].to_numpy().astype(float) - xc
            r["sparse"][f"{univ}_auc_coin_level"] = auc(xc, yu)
            r["sparse"][f"{univ}_auc_emp6h"] = auc(du["emp6h"].to_numpy().astype(float), yu)
            r["sparse"][f"{univ}_auc_moment_part"] = auc(xm, yu)
            r["sparse"][f"{univ}_n"] = int(len(du))
        # ── сделки ow_e по Z
        tw = pd.read_parquet(f"{W}/trades_ow_e.parquet")
        tw = tw[tw.side == side]
        sp = r["sparse"]["U1_emp6h_top20"]
        q = sp["threshold"]
        tz = tw["emp6h"] > q if q > 0 else tw["emp6h"] > 0
        tz = tz & tw["emp6h"].notna()
        def pnl(df):
            return {"n": int(len(df)), "sum": float(df["net"].sum()), "mean": float(df["net"].mean()) if len(df) else np.nan, "stop_share": float((df["kind"] == "stop").mean()) if len(df) else np.nan,
                    "neg_sum": float(df.loc[df["net"] < 0, "net"].sum())}
        r["trades_ow_e"] = {"all": pnl(tw), "Z": pnl(tw[tz]), "rest": pnl(tw[~tz]), "no_value": int(tw["emp6h"].isna().sum())}
        # ── подход к уровню: достижение расстояний
        appr = {}
        for dn in ("0p2", "0p1", "0p05"):
            col_s, col_m = f"reach_{dn}_s", f"mae_{dn}_bp"
            hit = d[col_s].notna()
            sub = d[hit]
            appr[dn] = {"reached": int(hit.sum()), "share": float(hit.mean()), "mean_time_s": float(sub[col_s].mean()) if len(sub) else np.nan,
                        "mean_mae_bp": float(sub[col_m].mean()) if len(sub) else np.nan, "o3_given_reached": float((sub[LABEL] == O3).mean()) if len(sub) else np.nan,
                        "o4_given_reached": float((sub[LABEL] == O4).mean()) if len(sub) else np.nan,
                        "o1_given_reached": float((sub[LABEL] == O1).mean()) if len(sub) else np.nan}
        r["approach_dist"] = appr
        # ── T от касания
        tw_ = {}
        for tp in (15, 60):
            for lab in ("x1", "n2"):
                col = f"tw_{lab}_t{tp}"
                v = d[col].dropna()
                tw_[f"{lab}_T{tp}"] = {"n_touched": int(len(v)), "break_first": float((v == "x").mean()) if len(v) else np.nan, "return_first": float((v == "r").mean()) if len(v) else np.nan,
                                       "neither": float((v == "n").mean()) if len(v) else np.nan}
        r["touch_window"] = tw_
        # ── число эпизодов / повторные
        sg = pd.read_parquet(f"{W}/signals_all.parquet")
        sg = sg[(sg.univ == "U1") & (sg.side == side)]
        r["signals"] = {"signals_u1": int(len(sg)), "episodes_u1": int(sg["ep_first"].sum()), "levels_with_signal": int(sg.groupby(["sym", "level"]).ngroups),
                        "repeat_episodes": int(sg["ep_first"].sum() - sg.groupby(["sym", "level"]).ngroups)}
    # ── собрать таблицы
    pd.concat(auc_rows).to_csv(f"{W}/auc.csv", index=False)
    pd.DataFrame(quint_rows).to_csv(f"{W}/quintiles.csv", index=False)
    # ── пересечения без сигнала
    cr = pd.read_csv(f"{W}/crossings.csv.gz")
    res["crossings"] = {}
    for side in SIDES:
        c = cr[cr.side == side]
        res["crossings"][side] = {"total": int(len(c)), "with_signal": int(c.with_signal.sum()), "without_signal": int((~c.with_signal).sum()),
                                  "without_by_open": c[~c.with_signal].open_class.value_counts().to_dict(), "with_by_open": c[c.with_signal].open_class.value_counts().to_dict(),
                                  "top_coins_without": c[~c.with_signal].sym.value_counts().head(15).to_dict()}
    # ── примеры владельца
    import datetime as dt
    ex = [("KORUUSDT", "long", "2026-10-12 04:22:36"), ("STXXUSDT", "long", "2026-10-14 18:07:02"), ("CLANKERUSDT", "long", "2026-10-15 22:27:04"),
          ("NXPCUSDT", "long", "2026-10-18 22:04:01"), ("WDCUSDT", "long", "2026-10-23 00:03:00"), ("ANIMEUSDT", "short", "2026-10-15 20:59:48")]
    sg = pd.read_parquet(f"{W}/signals_all.parquet")
    sg = sg[sg.univ == "U1"]
    exr = []
    for sym, side, ts in ex:
        t = dt.datetime.fromisoformat(ts).replace(tzinfo=dt.timezone(dt.timedelta(hours=4)))
        ms = int(t.timestamp() * 1000) - BASE_MS
        c = sg[(sg.sym == sym) & (sg.side == side)].copy()
        c["d"] = (c["sig_ms"] - ms).abs()
        c = c.sort_values("d")
        row = {"sym": sym, "side": side, "gmt4": ts}
        if len(c) and c.iloc[0]["d"] <= 120_000:
            h = c.iloc[0]
            e = ep[(ep.univ == "U1") & (ep.side == side) & (ep.sym == sym) & (ep.level == h["level"]) & (ep.ep_no == h["ep_no"])]
            row["found"] = True
            row["delta_s"] = float((h["sig_ms"] - ms) / 1000)
            if len(e):
                e = e.iloc[0]
                row["episode_first_gmt4"] = (dt.datetime.fromtimestamp((BASE_MS + int(e["sig_ms"])) / 1000, tz=dt.timezone(dt.timedelta(hours=4)))).strftime("%Y-%m-%d %H:%M:%S")
                for f in PRIM + ["emp2h", "tpm15", "mv15", "natr_bp", "pierce_max_bp", "spread_bp", "age_h"]:
                    row[f] = None if pd.isna(e[f]) else float(e[f])
                dd = U1[(U1.side == side) & U1["ok_t60"]]
                for f in ("emp6h", "tpm60", "mv5", "cons60", "pierce_ep", "spread_tau", "c5_btc_7d", "c5_eth_7d"):
                    xa = dd.loc[dd.half == "A", f].to_numpy().astype(float)
                    edg = edges_from(xa)
                    v = float(e[f]) if pd.notna(e[f]) else np.nan
                    row[f + "_bucket"] = None if np.isnan(v) else f"{int(np.searchsorted(edg, v, side='left'))}/{len(edg)}"
                row["outcome_x1_t60"] = OUTC.get(int(e[LABEL]), "нет") if int(e[LABEL]) else "нет (цензура)"
                row["traded_ow_e"] = bool(e["ep_traded_ow_e"])
            else:
                row["episode"] = "не найден в таблице эпизодов (ok_t60/tick)"
        else:
            row["found"] = False
        exr.append(row)
    res["examples"] = exr
    json.dump(res, open(f"{W}/results.json", "w"), ensure_ascii=False, indent=1, default=float)
    print("готово")


if __name__ == "__main__":
    main()
