#!/usr/bin/env python3
"""Анализ A (шум: длина эпизодов) и B (безубыточность: доли К / С / Т, p*) по таблицам ts_tables.npz. Результаты - малые csv в --out. Seed 37, B = 3000, 90 % интервалы (5 % и 95 %).

A. Эпизод (§5.2 + серия): сигнал (θ 25,2; k 3; p 1; взвод; Q1, Q3, Q4, Q7; дедупликация), длина D = число подряд идущих календарных минут с g >= g_entry одного знака, считая с минуты сигнала;
   серия, дошедшая до конца периода половины, цензурирована. P(D <= m) = (число D <= m без цензуры) / (число D > m или без цензуры) - оценка по «неотцензурированной» выборке (цензура только у сигналов
   за последние m минут периода; точный Каплан-Мейер печатается рядом для сверки). Кластер = актив x сутки сигнала. Серии подсказок (|s - b| >= θ, все минуты, без взвода) - таблица A_hints.
B. Исходы сделок: К = CV (схождение), С = стоп (S0 в B1/B3, S1 + S2 + S3 в V1/V2), Т = дедлайн TO; сделки, открытые на конце периода (END), в доли не входят.
   p*_C = n_ост / (n_ост - n_С): доля стопов, при которой средняя net = 0, при фактических n_ост (средняя net сделок без стопа) и n_С (средняя net стопа); p*_KC (только К и С) = n_К / (n_К - n_С).
   Фактическая доля - с интервалом Вильсона при эффективном n (кластерный бутстреп по активам) и бутстреп-интервалом (актив x сутки и актив).
"""
import argparse
import csv
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ts_stats as S      # noqa: E402

HOR = [1, 2, 5, 10, 30, 60]
T_DEAD = 1440
DEPTH_EDGES = [0.0, 50.0, 80.0, 120.0, 200.0, 1e18]
DEPTH_NAMES = ["<50", "50-80", "80-120", "120-200", ">=200"]
CODE = {"CV": 0, "S0": 1, "S1": 2, "S2": 3, "S3": 4, "SES": 5, "TO": 6, "END": 7}
HALF = {0: "F", 1: "W", 2: "F+W"}


def write(path, rows, cols):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(cols)
        for r in rows:
            w.writerow(["" if v is None else (("%.6g" % v) if isinstance(v, float) else v) for v in (r[c] for c in cols)])


def depth_bin(g):
    return np.searchsorted(np.asarray(DEPTH_EDGES), g, side="right") - 1


def surv_rows(ep, label_cols):
    """Строки таблицы A по группам: [(метки, маска)]."""
    D, cens = ep["D"], ep["cens"]
    n = len(D)
    cl_all = S.cluster_ids(ep["ai"], ep["day"])
    out = []
    for labels, m in label_cols:
        idx = np.flatnonzero(m)
        if len(idx) == 0:
            continue
        d, c = D[idx], cens[idx]
        cl = S.cluster_ids(ep["ai"][idx], ep["day"][idx])
        num = np.zeros((len(idx), len(HOR) + 1))
        den = np.zeros_like(num)
        for j, h in enumerate(HOR):
            elig = (d > h) | (c == 0)
            num[:, j] = ((d <= h) & (c == 0)) & elig
            den[:, j] = elig
        elig = (d >= T_DEAD) | (c == 0)                     # P(D >= T)
        num[:, len(HOR)] = (d >= T_DEAD) & elig
        den[:, len(HOR)] = elig
        pt, lo, hi, se, _ = S.ratio_boot(cl, num, den)
        cla = S.cluster_ids(ep["ai"][idx])                      # чувствительность: кластер = актив
        _, loa, hia, _, _ = S.ratio_boot(cla, num, den)
        # точный КМ для сверки
        km = [1.0 - S.km_at(d, c, h) for h in HOR] + [S.km_at(d, c, T_DEAD - 1)]
        row = dict(labels)
        row.update(n_ep=len(idx), n_cl=int(cl.max()) + 1, n_assets=len(np.unique(ep["ai"][idx])))
        for j, h in enumerate(HOR + [T_DEAD]):
            key = ("le%d" % h) if h != T_DEAD else "geT"
            row[key] = float(pt[j])
            row[key + "_lo"] = float(lo[j])
            row[key + "_hi"] = float(hi[j])
            row[key + "_km"] = float(km[j])
            row[key + "_loA"] = float(loa[j])
            row[key + "_hiA"] = float(hia[j])
        out.append(row)
    return out


def part_a(ep, hints, meta, outdir):
    g = ep["g_in"]
    db = depth_bin(g)
    half = ep["half"]
    byb = ep["byb"]
    groups = []
    for hv in (0, 1, 2):
        mh = np.ones(len(g), bool) if hv == 2 else (half == hv)
        for bv in ("все", "Bybit", "без Bybit"):
            mb = mh & (np.ones(len(g), bool) if bv == "все" else ((byb == 1) if bv == "Bybit" else (byb == 0)))
            groups.append((dict(half=HALF[hv], leg=bv, depth="все"), mb))
            for k, nm in enumerate(DEPTH_NAMES):
                groups.append((dict(half=HALF[hv], leg=bv, depth=nm), mb & (db == k)))
    rows = surv_rows(ep, groups)
    cols = ["half", "leg", "depth", "n_ep", "n_cl", "n_assets"] + [x for h in HOR for x in ("le%d" % h, "le%d_lo" % h, "le%d_hi" % h, "le%d_km" % h, "le%d_loA" % h, "le%d_hiA" % h)] + ["geT", "geT_lo", "geT_hi", "geT_km", "geT_loA", "geT_hiA"]
    write(os.path.join(outdir, "A_survival.csv"), rows, cols)
    # исходы эпизодов К / С / Т (EpisodeSim, §5.2) по глубине: доли с кластерным Вильсоном
    orows = []
    for hv in (0, 1, 2):
        mh = np.ones(len(g), bool) if hv == 2 else (half == hv)
        for bv in ("все", "Bybit", "без Bybit"):
            mb = mh & (np.ones(len(g), bool) if bv == "все" else ((byb == 1) if bv == "Bybit" else (byb == 0)))
            for k, nm in enumerate(["все"] + DEPTH_NAMES):
                m = mb if k == 0 else mb & (db == k - 1)
                idx = np.flatnonzero(m & (ep["out"] > 0))                 # out 0 = цензура (конец периода)
                if len(idx) == 0:
                    continue
                cl = S.cluster_ids(ep["ai"][idx])
                row = dict(half=HALF[hv], leg=bv, depth=nm, n=len(idx), n_assets=int(cl.max()) + 1)
                for o, on in ((1, "K"), (2, "C"), (3, "T")):
                    p, n, ne, lo, hi, se, ncl = S.cluster_wilson((ep["out"][idx] == o).astype(float), cl)
                    row.update({on: p, on + "_lo": lo, on + "_hi": hi, on + "_neff": ne})
                orows.append(row)
    write(os.path.join(outdir, "A_episode_outcomes.csv"), orows, ["half", "leg", "depth", "n", "n_assets", "K", "K_lo", "K_hi", "K_neff", "C", "C_lo", "C_hi", "C_neff", "T", "T_lo", "T_hi", "T_neff"])
    return rows, orows


def part_a_hints(hints, meta, outdir):
    """Серии |s - b| >= θ (отчёт подсказок): доля длины 1, 2, >=3 минут. Кластер = актив x сутки старта. Строки: θ x половина x набор ячеек (все 28 / три ячейки подсказок / с Bybit / без Bybit)."""
    cells = meta["cells"]
    nb = len(meta["hb_edges"]) + 1
    hint_cells = {"binance~bingx/crypto", "bingx~bitget/crypto", "aster~binance/crypto"}
    sel = {"все 28 ячеек": np.ones(len(cells), bool), "три ячейки отчёта подсказок": np.array([c in hint_cells for c in cells]),
           "ячейки с Bybit": np.array(["bybit" in c for c in cells]), "ячейки без Bybit": np.array(["bybit" not in c for c in cells])}
    rows = []
    for hv in (0, 1, 2):
        for nm, cm in sel.items():
            for ti, th in enumerate(meta["thetas"]):
                m = (hints[:, 2] == ti) & cm[hints[:, 4]]
                if hv < 2:
                    m &= hints[:, 3] == hv
                h = hints[m]
                if len(h) == 0:
                    continue
                u, c = h[:, 5:5 + nb].astype(float), h[:, 5 + nb:5 + 2 * nb].astype(float)
                tot = u.sum(1) + c[:, 1:].sum(1)
                n1, n2 = u[:, 0], u[:, 1]
                n3 = u[:, 2:].sum(1) + c[:, 1:].sum(1)
                cl = S.cluster_ids(h[:, 0], h[:, 1])
                pt, lo, hi, se, _ = S.ratio_boot(cl, np.column_stack([n1, n2, n3]), np.column_stack([tot, tot, tot]))
                rows.append(dict(half=HALF[hv], cells=nm, theta=th, n_ep=int(tot.sum()), n_cl=int(cl.max()) + 1, d1=float(pt[0]), d1_lo=float(lo[0]), d1_hi=float(hi[0]), d2=float(pt[1]),
                                 d3p=float(pt[2]), d3p_lo=float(lo[2]), d3p_hi=float(hi[2])))
    write(os.path.join(outdir, "A_hints.csv"), rows, ["half", "cells", "theta", "n_ep", "n_cl", "d1", "d1_lo", "d1_hi", "d2", "d3p", "d3p_lo", "d3p_hi"])
    return rows


# ------------------------------------------------------------------ B
def outcome_masks(tr):
    code = tr["code"]
    return dict(K=code == CODE["CV"], C=np.isin(code, [CODE["S0"], CODE["S1"], CODE["S2"], CODE["S3"]]), T=code == CODE["TO"], END=code == CODE["END"])


def breakeven(net, K, C, T):
    """-> n_K, n_C, n_rest, q_C, p*_C (по остальным), p*_KC (К против С)."""
    nK = float(net[K].mean()) if K.any() else float("nan")
    nC = float(net[C].mean()) if C.any() else float("nan")
    rest = K | T
    nR = float(net[rest].mean()) if rest.any() else float("nan")
    n = K.sum() + C.sum() + T.sum()
    qC = float(C.sum()) / n if n else float("nan")
    ps_c = nR / (nR - nC) if (np.isfinite(nR) and np.isfinite(nC) and nR - nC != 0) else float("nan")
    ps_kc = nK / (nK - nC) if (np.isfinite(nK) and np.isfinite(nC) and nK - nC != 0) else float("nan")
    return nK, nC, nR, qC, ps_c, ps_kc


CSETS = {"V1": {"все стопы S1+S2+S3": [2, 3, 4], "только S1": [2]}, "V2": {"все стопы S1+S2+S3": [2, 3, 4], "только S1": [2]},
         "B1": {"все стопы S0+S2+S3": [1, 3, 4], "только S0": [1]}, "B3": {"все стопы S0+S2+S3": [1, 3, 4], "только S0": [1]}}


def part_b(tr, outdir):
    uni_names = ["B1", "B3", "V1", "V2"]
    db = depth_bin(tr["g_in"])
    code = tr["code"]
    rows = []
    for ui, un in enumerate(uni_names):
        for cname, ccodes in CSETS[un].items():
            Cm = np.isin(code, ccodes)
            Km = code == CODE["CV"]
            Tm = code == CODE["TO"]
            Em = code == CODE["END"]
            for hv in (0, 1, 2):
                base = (tr["uni"] == ui) & ((tr["half"] == hv) if hv < 2 else True) & ~Em
                for k, nm in enumerate(["все"] + DEPTH_NAMES):
                    m = base if k == 0 else base & (db == k - 1)
                    idx = np.flatnonzero(m)
                    if len(idx) < 5:
                        continue
                    K, C, Tt = Km[idx], Cm[idx], Tm[idx] | (np.isin(code[idx], [1, 2, 3, 4]) & ~Cm[idx])        # «Т» включает прочие стопы, не вошедшие в выбранное С (для «только S1»)
                    net, gross, fund = tr["net"][idx], tr["gross"][idx], tr["fund"][idx]
                    a_cl = S.cluster_ids(tr["ai"][idx])
                    ad_cl = S.cluster_ids(tr["ai"][idx], tr["day"][idx])
                    nK, nC, nR, qC, ps_c, ps_kc = breakeven(net, K, C, Tt)
                    row = dict(uni=un, cset=cname, half=HALF[hv], depth=nm, n=len(idx), n_assets=int(a_cl.max()) + 1, n_ad=int(ad_cl.max()) + 1, n_K=int(K.sum()), n_C=int(C.sum()), n_T=int(Tt.sum()))
                    for y, nmn in ((K, "qK"), (C, "qC"), (Tt, "qT")):
                        p, n_, ne, lo, hi, se, ncl = S.cluster_wilson(y.astype(float), a_cl)
                        row.update({nmn: p, nmn + "_lo": lo, nmn + "_hi": hi, nmn + "_neff": ne})
                    _, lo2, hi2, _, _ = S.ratio_boot(ad_cl, C.astype(float), np.ones(len(idx)))
                    row.update(qC_ad_lo=float(lo2[0]), qC_ad_hi=float(hi2[0]))
                    row.update(gross_K=float(gross[K].mean()) if K.any() else None, loss_C_gross=float(-gross[C].mean()) if C.any() else None, fee=12.6, fund_all=float(fund.mean()),
                               fund_K=float(fund[K].mean()) if K.any() else None, fund_C=float(fund[C].mean()) if C.any() else None, net_K=nK, net_C=nC, net_rest=nR, net_mean=float(net.mean()))
                    row.update(pstar_C=ps_c, pstar_KC=ps_kc, diff_C=qC - ps_c if np.isfinite(ps_c) else None)
                    if C.sum() > 0 and (K | Tt).sum() > 0:
                        res = boot_diff(net, K, C, Tt, a_cl)
                        row.update(diff_lo=res[0], diff_hi=res[1], pstar_lo=res[2], pstar_hi=res[3])
                    _, nlo, nhi, _, _ = S.ratio_boot(a_cl, net, np.ones(len(idx)))
                    row.update(net_lo=float(nlo[0]), net_hi=float(nhi[0]))
                    rows.append(row)
    cols = ["uni", "cset", "half", "depth", "n", "n_assets", "n_ad", "n_K", "n_C", "n_T", "qK", "qK_lo", "qK_hi", "qK_neff", "qC", "qC_lo", "qC_hi", "qC_neff", "qC_ad_lo", "qC_ad_hi", "qT", "qT_lo", "qT_hi", "qT_neff",
            "gross_K", "loss_C_gross", "fee", "fund_all", "fund_K", "fund_C", "net_K", "net_C", "net_rest", "net_mean", "net_lo", "net_hi", "pstar_C", "pstar_KC", "pstar_lo", "pstar_hi", "diff_C", "diff_lo", "diff_hi"]
    for r in rows:
        for c in cols:
            r.setdefault(c, None)
    write(os.path.join(outdir, "B_breakeven.csv"), rows, cols)
    return rows


def boot_diff(net, K, C, Tt, cl, B=S.B_BOOT, seed=S.SEED):
    """Бутстреп по активам: q_C - p*_C (p*_C = n_ост/(n_ост - n_С)). -> (низ, верх разности; низ, верх p*)."""
    n_cl = int(cl.max()) + 1
    cols = [np.bincount(cl, weights=x, minlength=n_cl) for x in (net * (K | Tt), (K | Tt).astype(float), net * C, C.astype(float))]
    SR, NR, SC, NC = cols
    diffs, ps = [], []
    for w in S.boot_weights(n_cl, B, seed):
        sr, nr, sc, nc = w @ SR, w @ NR, w @ SC, w @ NC
        with np.errstate(invalid="ignore", divide="ignore"):
            nR, nCm = sr / nr, sc / nc
            pstar = nR / (nR - nCm)
            q = nc / (nr + nc)
        diffs.append(q - pstar)
        ps.append(pstar)
    diffs, ps = np.concatenate(diffs), np.concatenate(ps)
    return (float(np.nanquantile(diffs, 0.05)), float(np.nanquantile(diffs, 0.95)), float(np.nanquantile(ps, 0.05)), float(np.nanquantile(ps, 0.95)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tables", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--parts", default="A,B")
    a = ap.parse_args()
    tr, ep, hints, act, meta = S.load_tables(a.tables)
    os.makedirs(a.out, exist_ok=True)
    if "A" in a.parts:
        part_a(ep, hints, meta, a.out)
        part_a_hints(hints, meta, a.out)
    if "B" in a.parts:
        part_b(tr, a.out)


if __name__ == "__main__":
    main()
