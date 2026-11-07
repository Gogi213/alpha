#!/usr/bin/env python3
"""Анализ C: типы сделок по признакам ДО входа (правила - ts_types.py, границы - types_cuts.json, закоммичены до чтения исходов). Универсумы B1 и B3.

Для каждого типа и каждого набора (F, W, F+W): n сделок, число различных активов (кластеров), доли К / С / Т, средняя net, gross, фандинг; интервалы - бутстреп по АКТИВАМ (seed 37, B = 3000, 90 %).
Безубыточная доля К: q*_K = -n_неК / (n_К - n_неК) (при n_К > 0 > n_неК); разность q_K - q*_K > 0  <=>  средняя net > 0 (тождество), поэтому проверяется средняя net и q_K - q*_K.
Многократность: max-t бутстреп (Westfall-Young) по всему семейству типов с n >= 30 и >= 10 активов в наборе; скорректированное p = доля ресэмплов, где максимум z* >= наблюдаемого z.
Проверка «определено на формировании - подтверждено на окне»: из типов с n_F >= 30 и >= 10 активов берутся TOP_K (назначено, 10) с наибольшей нижней границей 90 %-интервала средней net на F;
их результаты на W - интервал по активам W, p (односторонний) и Холм на TOP_K.
"""
import argparse
import csv
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ts_stats as S       # noqa: E402
import ts_types as TY      # noqa: E402

CODE = {"CV": 0, "S0": 1, "S1": 2, "S2": 3, "S3": 4, "SES": 5, "TO": 6, "END": 7}
UNI = {"B1": 0, "B3": 1}
TOP_K = 10
HALFN = {0: "F", 1: "W", 2: "F+W"}


def prep(tr, uni):
    m = (tr["uni"] == uni) & (tr["code"] != CODE["END"])
    c = {k: v[m] for k, v in tr.items()}
    code = c["code"]
    c["K"] = (code == CODE["CV"]).astype(float)
    c["C"] = np.isin(code, [1, 2, 3, 4]).astype(float)
    c["T"] = (code == CODE["TO"]).astype(float)
    return c


def asset_sums(c, mask, nA):
    """По активам: n, Σnet, Σgross, Σfund, ΣK, ΣC, ΣT, Σnet·K, Σnet·(1-K), n·(1-K)."""
    ai = c["ai"].astype(np.int64)
    out = {}
    out["n"] = np.bincount(ai, weights=mask.astype(float), minlength=nA)
    for nm, w in (("net", c["net"]), ("gross", c["gross"]), ("fund", c["fund"]), ("K", c["K"]), ("C", c["C"]), ("T", c["T"]), ("netK", c["net"] * c["K"]), ("netNK", c["net"] * (1 - c["K"])), ("nNK", 1 - c["K"])):
        out[nm] = np.bincount(ai, weights=w * mask, minlength=nA)
    return out


def eval_types(c, fam, hv, nA, B=S.B_BOOT):
    """Статистика всех типов семейства на наборе hv. -> (имена, матрица точечных оценок, матрица ресэмплов mean_net [B, M], сводка)."""
    sel = np.ones(len(c["net"]), bool) if hv == 2 else (c["half"] == hv)
    names, rows, A_sums = [], [], []
    for nm, m in fam.items():
        mm = m & sel
        n = int(mm.sum())
        if n < TY.MIN_TRADES:
            continue
        s = asset_sums(c, mm, nA)
        na = int((s["n"] > 0).sum())
        if na < TY.MIN_ASSETS:
            continue
        names.append(nm)
        A_sums.append(s)
    return names, A_sums


def summarize(names, sums, B=S.B_BOOT, seed=S.SEED):
    """По списку сумм по активам считает точечные оценки и бутстреп (общие веса активов для всех типов - сохраняет зависимость между типами)."""
    M = len(names)
    if M == 0:
        return None
    nA = len(sums[0]["n"])
    keys = ["n", "net", "gross", "fund", "K", "C", "T", "netK", "netNK", "nNK"]
    X = {k: np.stack([s[k] for s in sums], axis=1) for k in keys}       # [nA, M]
    tot = {k: X[k].sum(0) for k in keys}
    point = dict(n=tot["n"], assets=(X["n"] > 0).sum(0), net=tot["net"] / tot["n"], gross=tot["gross"] / tot["n"], fund=tot["fund"] / tot["n"], qK=tot["K"] / tot["n"], qC=tot["C"] / tot["n"], qT=tot["T"] / tot["n"])
    with np.errstate(invalid="ignore", divide="ignore"):
        nK = tot["netK"] / tot["K"]
        nNK = tot["netNK"] / tot["nNK"]
        point["net_K"], point["net_nK"] = nK, nNK
        point["qK_star"] = np.where((nK > 0) & (nNK < 0), -nNK / (nK - nNK), np.where(nNK >= 0, 0.0, np.nan))
        point["diff"] = point["qK"] - point["qK_star"]
    boot_net, boot_diff = [], []
    for w in S.boot_weights(nA, B, seed):
        N = w @ X["n"]
        with np.errstate(invalid="ignore", divide="ignore"):
            bn = (w @ X["net"]) / N
            bK = (w @ X["K"]) / N
            k_n = (w @ X["netK"]) / (w @ X["K"])
            nk_n = (w @ X["netNK"]) / (w @ X["nNK"])
            qs = np.where((k_n > 0) & (nk_n < 0), -nk_n / (k_n - nk_n), np.where(nk_n >= 0, 0.0, np.nan))
        boot_net.append(bn)
        boot_diff.append(bK - qs)
    return point, np.vstack(boot_net), np.vstack(boot_diff)


def maxt_p(point_net, boot_net):
    """Max-t скорректированное одностороннее p (H1: средняя net > 0) и z наблюдаемое."""
    se = np.nanstd(boot_net, axis=0, ddof=1)
    z = point_net / np.where(se > 0, se, np.nan)
    zb = (boot_net - point_net[None, :]) / np.where(se > 0, se, np.nan)[None, :]
    mx = np.nanmax(zb, axis=1)
    p_adj = np.array([(np.sum(mx >= zi) + 1.0) / (len(mx) + 1.0) if np.isfinite(zi) else np.nan for zi in z])
    p_raw = np.array([(np.sum(zb[:, j] >= z[j]) + 1.0) / (zb.shape[0] + 1.0) if np.isfinite(z[j]) else np.nan for j in range(len(z))])
    return z, p_raw, p_adj, se


def holm(p):
    p = np.asarray(p, float)
    o = np.argsort(p)
    m = len(p)
    adj = np.empty(m)
    run = 0.0
    for r, i in enumerate(o):
        run = max(run, (m - r) * p[i])
        adj[i] = min(1.0, run)
    return adj


CONTRASTS = [("M1hi", "M1lo"), ("M3hi", "M3lo"), ("M7hi", "M7lo"), ("V3hi", "V3lo"), ("F3hi", "F3lo"), ("DR7hi", "DR7lo"), ("BYS", "BYL"), ("BY", "~BY"), ("ONE", "BAL"), ("PUMP", "DUMP"),
             ("FA", "FF"), ("FAS", "FFS"), ("ST", "~ST"), ("LQlo", "LQhi"), ("DEP3|DEP4|DEP5", "DEP1|DEP2"), ("C_STRUCT", "~C_STRUCT"), ("C_PRESS", "~C_PRESS"), ("C_NOISE", "~C_NOISE"), ("DBY", "~DBY"),
             ("AGE1", "AGE0")]


def resolve(expr, at, comps, d):
    if expr.startswith("~"):
        return ~resolve(expr[1:], at, comps, d)
    out = None
    for part in expr.split("|"):
        m = at[part] if part in at else comps[part]
        out = m if out is None else (out | m)
    return out


def contrasts(c, at, comps, un, nA, d, B=S.B_BOOT, seed=S.SEED):
    """Разность типов А - Б: доли К (п.п.) и средней net (bps), 90 % интервал - бутстреп по активам (общие веса для обоих типов и обеих половин)."""
    rows = []
    for hv in (0, 1, 2):
        sel = np.ones(len(c["net"]), bool) if hv == 2 else (c["half"] == hv)
        for ea, eb in CONTRASTS:
            ma, mb = resolve(ea, at, comps, d) & sel, resolve(eb, at, comps, d) & sel
            if ma.sum() < TY.MIN_TRADES or mb.sum() < TY.MIN_TRADES:
                continue
            sa, sb = asset_sums(c, ma, nA), asset_sums(c, mb, nA)
            if (sa["n"] > 0).sum() < TY.MIN_ASSETS or (sb["n"] > 0).sum() < TY.MIN_ASSETS:
                continue
            pa_k, pb_k = sa["K"].sum() / sa["n"].sum(), sb["K"].sum() / sb["n"].sum()
            pa_n, pb_n = sa["net"].sum() / sa["n"].sum(), sb["net"].sum() / sb["n"].sum()
            dk, dn = [], []
            for w in S.boot_weights(nA, B, seed):
                with np.errstate(invalid="ignore", divide="ignore"):
                    dk.append((w @ sa["K"]) / (w @ sa["n"]) - (w @ sb["K"]) / (w @ sb["n"]))
                    dn.append((w @ sa["net"]) / (w @ sa["n"]) - (w @ sb["net"]) / (w @ sb["n"]))
            dk, dn = np.concatenate(dk), np.concatenate(dn)
            rows.append(dict(uni=un, set=HALFN[hv], A=ea, B=eb, nA=int(sa["n"].sum()), nB=int(sb["n"].sum()), assetsA=int((sa["n"] > 0).sum()), assetsB=int((sb["n"] > 0).sum()),
                             qK_A=pa_k, qK_B=pb_k, dqK=pa_k - pb_k, dqK_lo=float(np.nanquantile(dk, 0.05)), dqK_hi=float(np.nanquantile(dk, 0.95)), net_A=pa_n, net_B=pb_n, dnet=pa_n - pb_n,
                             dnet_lo=float(np.nanquantile(dn, 0.05)), dnet_hi=float(np.nanquantile(dn, 0.95))))
    return rows


def write(path, rows, cols):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(cols)
        for r in rows:
            w.writerow(["" if r.get(c) is None else (("%.6g" % r[c]) if isinstance(r[c], (float, np.floating)) else r[c]) for c in cols])


COLS = ["uni", "set", "type", "n", "assets", "qK", "qK_star", "diff", "diff_lo", "diff_hi", "qC", "qT", "gross", "fund", "net", "net_lo", "net_hi", "net_K", "net_nK", "z", "p_raw", "p_maxt"]


def rows_for(un, hv, names, point, bnet, bdiff, with_p=True):
    z, p_raw, p_adj, se = maxt_p(point["net"], bnet)
    rows = []
    for j, nm in enumerate(names):
        rows.append(dict(uni=un, set=HALFN[hv], type=nm, n=int(point["n"][j]), assets=int(point["assets"][j]), qK=point["qK"][j], qK_star=point["qK_star"][j], diff=point["diff"][j],
                         diff_lo=float(np.nanquantile(bdiff[:, j], 0.05)), diff_hi=float(np.nanquantile(bdiff[:, j], 0.95)), qC=point["qC"][j], qT=point["qT"][j], gross=point["gross"][j],
                         fund=point["fund"][j], net=point["net"][j], net_lo=float(np.nanquantile(bnet[:, j], 0.05)), net_hi=float(np.nanquantile(bnet[:, j], 0.95)), net_K=point["net_K"][j],
                         net_nK=point["net_nK"][j], z=z[j], p_raw=p_raw[j], p_maxt=p_adj[j]))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tables", required=True)
    ap.add_argument("--cuts", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    tr, ep, hints, act, meta = S.load_tables(a.tables)
    cuts = json.load(open(a.cuts, encoding="utf-8"))
    nA = len(meta["assets"])
    allrows, sel_rows, summ, cons, contr_rows, sigrows = [], [], [], [], [], []
    for un, ui in UNI.items():
        c = prep(tr, ui)
        d = TY.derive(c)
        at = TY.atoms(d, cuts)
        comps = TY.composites(at, d)
        fam = TY.family(at, comps)
        simple = {k: v for k, v in fam.items() if "&" not in k}
        contr_rows += contrasts(c, at, comps, un, nA, d)
        res = {}
        for hv in (0, 1, 2):
            # одиночные и составные типы: полные строки; все пары: только для поиска
            names_s, sums_s = eval_types(c, simple, hv, nA)
            pt, bn, bd = summarize(names_s, sums_s)
            allrows += rows_for(un, hv, names_s, pt, bn, bd)
            names_f, sums_f = eval_types(c, fam, hv, nA)
            ptf, bnf, bdf = summarize(names_f, sums_f)
            res[hv] = (names_f, ptf, bnf, bdf)
            z, p_raw, p_adj, se = maxt_p(ptf["net"], bnf)
            lcb = np.nanquantile(bnf, 0.05, axis=0)
            summ.append(dict(uni=un, set=HALFN[hv], types_tested=len(names_f), types_gt0_raw=int(np.sum(lcb > 0)), types_p_raw_lt05=int(np.sum(p_raw < 0.05)), types_p_maxt_lt05=int(np.sum(p_adj < 0.05)),
                             max_net=float(np.nanmax(ptf["net"])), max_net_type=names_f[int(np.nanargmax(ptf["net"]))], max_z=float(np.nanmax(z))))
            if hv != 2:
                allrows += [dict(r, type="FAM:" + r["type"]) for r in rows_for(un, hv, names_f, ptf, bnf, bdf) if "&" in r["type"] and r["net_lo"] > -5]    # пары с нижней границей > -5 bps
        # --- все типы, значимые после max-t: на F, на F+W; их результаты на каждой половине
        nA_, pA_, bA_, dA_ = res[2]
        zA, prA, padjA, seA = maxt_p(pA_["net"], bA_)
        nF0, pF0, bF0, dF0 = res[0]
        nW0, pW0, bW0, dW0 = res[1]
        zF0, prF0, padjF0, seF0 = maxt_p(pF0["net"], bF0)
        iF0 = {nm: j for j, nm in enumerate(nF0)}
        iW0 = {nm: j for j, nm in enumerate(nW0)}
        for src, names_s, padj_s, pt_s, bt_s in (("F", nF0, padjF0, pF0, bF0), ("F+W", nA_, padjA, pA_, bA_)):
            for j, nm in enumerate(names_s):
                if padj_s[j] < 0.05:
                    r = dict(uni=un, found_on=src, type=nm, p_maxt=padj_s[j], n=int(pt_s["n"][j]), assets=int(pt_s["assets"][j]), net=pt_s["net"][j], net_lo=float(np.nanquantile(bt_s[:, j], 0.05)),
                             qK=pt_s["qK"][j], qK_star=pt_s["qK_star"][j])
                    for hn, ii, pp, bb in (("F", iF0, pF0, bF0), ("W", iW0, pW0, bW0)):
                        if nm in ii:
                            jj = ii[nm]
                            r.update({"n_" + hn: int(pp["n"][jj]), "assets_" + hn: int(pp["assets"][jj]), "net_" + hn: pp["net"][jj], "lo_" + hn: float(np.nanquantile(bb[:, jj], 0.05)),
                                      "hi_" + hn: float(np.nanquantile(bb[:, jj], 0.95))})
                    sigrows.append(r)
        # --- отбор на F -> проверка на W
        nF, pF, bF, dF = res[0]
        nW, pW, bW, dW = res[1]
        # согласие половин по всем типам: ранговая корреляция и наклон net_W на net_F; судьба типов, значимых на F после max-t
        idxW0 = {nm: j for j, nm in enumerate(nW)}
        common = [(j, idxW0[nm]) for j, nm in enumerate(nF) if nm in idxW0]
        zF, praw, padjF, seF = maxt_p(pF["net"], bF)
        if common:
            xf = np.array([pF["net"][j] for j, _ in common])
            xw = np.array([pW["net"][jw] for _, jw in common])
            rk = lambda v: np.argsort(np.argsort(v)).astype(float)
            spear = float(np.corrcoef(rk(xf), rk(xw))[0, 1])
            slope = float(np.cov(xf, xw)[0, 1] / np.var(xf, ddof=1))
            fs = [(j, jw) for (j, jw) in common if padjF[j] < 0.05]
            fpos = [(j, jw) for (j, jw) in common if np.nanquantile(bF[:, j], 0.05) > 0]
            fw_sig = [(j, jw) for (j, jw) in fpos if np.nanquantile(bW[:, jw], 0.05) > 0]
            cons.append(dict(uni=un, types_common=len(common), spearman_FW=spear, slope_FW=slope, F_lcb_gt0=len(fpos), F_maxt_sig=len(fs),
                             F_maxt_sig_mean_netF=float(np.mean([pF["net"][j] for j, _ in fs])) if fs else None, F_maxt_sig_mean_netW=float(np.mean([pW["net"][jw] for _, jw in fs])) if fs else None,
                             F_maxt_sig_W_net_gt0=int(sum(pW["net"][jw] > 0 for _, jw in fs)) if fs else None, F_lcb_gt0_W_lcb_gt0=len(fw_sig),
                             F_lcb_gt0_mean_netF=float(np.mean([pF["net"][j] for j, _ in fpos])) if fpos else None, F_lcb_gt0_mean_netW=float(np.mean([pW["net"][jw] for _, jw in fpos])) if fpos else None))
        lcbF = np.nanquantile(bF, 0.05, axis=0)
        order = np.argsort(-np.where(np.isfinite(lcbF), lcbF, -1e18))[:TOP_K]
        idxW = {nm: j for j, nm in enumerate(nW)}
        pvals = []
        tmp = []
        for j in order:
            nm = nF[j]
            if nm in idxW:
                jw = idxW[nm]
                se = np.nanstd(bW[:, jw], ddof=1)
                z = pW["net"][jw] / se if se > 0 else float("nan")
                p1 = (np.sum((bW[:, jw] - pW["net"][jw]) / se >= z) + 1.0) / (bW.shape[0] + 1.0)
                tmp.append((nm, j, jw, p1))
                pvals.append(p1)
            else:
                tmp.append((nm, j, None, float("nan")))
                pvals.append(1.0)
        adj = holm(pvals)
        for (nm, j, jw, p1), ad in zip(tmp, adj):
            r = dict(uni=un, type=nm, n_F=int(pF["n"][j]), assets_F=int(pF["assets"][j]), net_F=pF["net"][j], net_F_lo=float(lcbF[j]), qK_F=pF["qK"][j], qK_star_F=pF["qK_star"][j])
            if jw is not None:
                dfw = bF[:, j] - bW[:, jw]               # одни и те же ресэмплы активов (одинаковый seed): парная разность формирование - окно
                r.update(diffFW=pF["net"][j] - pW["net"][jw], diffFW_lo=float(np.nanquantile(dfw, 0.05)), diffFW_hi=float(np.nanquantile(dfw, 0.95)))
                r.update(n_W=int(pW["n"][jw]), assets_W=int(pW["assets"][jw]), net_W=pW["net"][jw], net_W_lo=float(np.nanquantile(bW[:, jw], 0.05)), net_W_hi=float(np.nanquantile(bW[:, jw], 0.95)),
                         qK_W=pW["qK"][jw], qK_star_W=pW["qK_star"][jw], diff_W=pW["diff"][jw], p_W=p1, p_W_holm=ad)
            else:
                r.update(n_W=0, assets_W=0)
            sel_rows.append(r)
    os.makedirs(a.out, exist_ok=True)
    write(os.path.join(a.out, "C_types.csv"), allrows, COLS)
    write(os.path.join(a.out, "C_select_F_to_W.csv"), sel_rows, ["uni", "type", "n_F", "assets_F", "net_F", "net_F_lo", "qK_F", "qK_star_F", "n_W", "assets_W", "net_W", "net_W_lo", "net_W_hi", "qK_W", "qK_star_W",
                                                                  "diff_W", "p_W", "p_W_holm", "diffFW", "diffFW_lo", "diffFW_hi"])
    write(os.path.join(a.out, "C_consistency_F_W.csv"), cons, ["uni", "types_common", "spearman_FW", "slope_FW", "F_lcb_gt0", "F_maxt_sig", "F_maxt_sig_mean_netF", "F_maxt_sig_mean_netW", "F_maxt_sig_W_net_gt0",
                                                           "F_lcb_gt0_W_lcb_gt0", "F_lcb_gt0_mean_netF", "F_lcb_gt0_mean_netW"])
    write(os.path.join(a.out, "C_family_sig.csv"), sigrows, ["uni", "found_on", "type", "p_maxt", "n", "assets", "net", "net_lo", "qK", "qK_star", "n_F", "assets_F", "net_F", "lo_F", "hi_F", "n_W", "assets_W", "net_W", "lo_W", "hi_W"])
    write(os.path.join(a.out, "C_contrasts.csv"), contr_rows, ["uni", "set", "A", "B", "nA", "nB", "assetsA", "assetsB", "qK_A", "qK_B", "dqK", "dqK_lo", "dqK_hi", "net_A", "net_B", "dnet", "dnet_lo", "dnet_hi"])
    write(os.path.join(a.out, "C_family_summary.csv"), summ, ["uni", "set", "types_tested", "types_gt0_raw", "types_p_raw_lt05", "types_p_maxt_lt05", "max_net", "max_net_type", "max_z"])


if __name__ == "__main__":
    main()
