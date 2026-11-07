#!/usr/bin/env python3
"""Анализ E: (1) строка (б): стопы S1 / S2 / S3 / дедлайн у V1 и V2 - точный тест «Bybit», стратифицированный по активам (устойчив к кластеризации), независимые эпизоды;
(2) предсказуемость до входа: логистическая регрессия P(К | признаки) и гребневая регрессия net, обучение на F, проверка на W (AUC с кластерным по активам интервалом, net верхнего дециля).
Регуляризация назначена (логит λ = 10 на стандартизованных признаках, гребень λ = 1000 · n_обуч/1000 ... см. LAM_*) - назначено CEO без Судьи, подтвердить; результат от неё не зависит качественно (проверено λ x 10, / 10).
"""
import argparse
import csv
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ts_stats as S       # noqa: E402
import ts_types as TY      # noqa: E402

CODE = {"CV": 0, "S0": 1, "S1": 2, "S2": 3, "S3": 4, "SES": 5, "TO": 6, "END": 7}
LAM_LOGIT = 10.0
LAM_RIDGE = 1000.0
NBINS = 1000


def log_comb(n, k):
    if k < 0 or k > n:
        return -math.inf
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def hyper_pmf(m, mB, s):
    """P(j стопов на Bybit-сделках среди s стопов, размещённых случайно среди m сделок актива, из которых mB Bybit), j = 0..s."""
    return np.array([math.exp(log_comb(mB, j) + log_comb(m - mB, s - j) - log_comb(m, s)) if (0 <= j <= mB and 0 <= s - j <= m - mB) else 0.0 for j in range(s + 1)])


def stratified_bybit_test(tr, ui, hv, stop_codes, flag="byb"):
    """Сосредоточены ли стопы на сделках с признаком flag (нога Bybit)? Нулевая гипотеза: внутри КАЖДОГО актива стопы размещены случайно среди его сделок (учёт гетерогенности активов).
    Число стопов на Bybit = сумма независимых гипергеометрических по активам; p = P(сумма >= факт) - точная свёртка."""
    m = (tr["uni"] == ui) & (tr["half"] == hv) & (tr["code"] != CODE["END"])
    ai, byb, code = tr["ai"][m].astype(int), tr[flag][m] == 1, tr["code"][m]
    stop = np.isin(code, stop_codes)
    pmf = np.array([1.0])
    mean = var = 0.0
    n_stop_assets = 0
    for a in np.unique(ai[stop]):
        mm = ai == a
        s = int((stop & mm).sum())
        mB = int((byb & mm).sum())
        mt = int(mm.sum())
        n_stop_assets += 1
        p = hyper_pmf(mt, mB, s)
        pmf = np.convolve(pmf, p)
        js = np.arange(s + 1)
        e1 = float((p * js).sum())
        mean += e1
        var += float((p * js * js).sum()) - e1 * e1
    obs = int((stop & byb).sum())
    p_up = float(pmf[obs:].sum()) if obs < len(pmf) else 0.0
    return dict(uni=ui, half=hv, flag=flag, trades=int(m.sum()), flag_trades=int(byb.sum()), stops=int(stop.sum()), stops_on_flag=obs, stops_expected=mean, stops_sd=math.sqrt(max(var, 0.0)), stop_assets=n_stop_assets,
                p_strat=p_up, rate_flag=float((stop & byb).sum() / max(byb.sum(), 1)), rate_noflag=float((stop & ~byb).sum() / max((~byb).sum(), 1)))


def independent_episodes(tr, ui, hv, stop_codes, gap=1440):
    """Стопы одного актива, ближе друг к другу, чем gap минут, - один эпизод (кластеризация по минуте сигнала)."""
    m = (tr["uni"] == ui) & (tr["half"] == hv) & np.isin(tr["code"], stop_codes)
    ai, t = tr["ai"][m].astype(int), tr["t_sig"][m]
    n_ep = 0
    for a in np.unique(ai):
        ts = np.sort(t[ai == a])
        n_ep += 1 + int((np.diff(ts) > gap).sum())
    return int(m.sum()), len(np.unique(ai)), n_ep


# ------------------------------------------------------------------ модели
def design(d, fit=None):
    """Матрица признаков (только прошлое). fit=None - обучение: медианы, средние, sd, список столбцов с пропусками; иначе применить fit к другой выборке."""
    cols = {}
    with np.errstate(invalid="ignore", divide="ignore"):
        cols["lg"] = np.log(d["g_in"])
        cols["lgs"] = np.log(np.maximum(d["gsig"], 1e-9))
        for k in ("M1", "M3", "M7", "V3d", "drift7"):
            cols["l" + k] = np.log1p(d[k])
        cols["F3"] = d["F3"]
        cols["fund"] = np.clip(d["fund_day"], -50, 50) / 10.0
        cols["stale"] = d["stale_hi"].astype(float)
        cols["share"] = d["share_drv"]
        cols["pump"] = d["pump"].astype(float)
        cols["byb"] = (d["byb"] == 1).astype(float)
        cols["bys"] = d["byb_short"].astype(float)
        cols["byl"] = d["byb_long"].astype(float)
        cols["bx"] = d["bingx_any"].astype(float)
        cols["dby"] = d["drv_bybit"].astype(float)
        cols["lturn"] = np.log1p(d["minturn"])
        cols["age0"] = (d["age"] == 0).astype(float)
        cols["hs"] = np.sin(2 * np.pi * d["hour"] / 24)
        cols["hc"] = np.cos(2 * np.pi * d["hour"] / 24)
    names = list(cols)
    X = np.column_stack([cols[k] for k in names])
    miss = ~np.isfinite(X)
    if fit is None:
        mc = np.flatnonzero(miss.any(0))
        med = np.nanmedian(np.where(np.isfinite(X), X, np.nan), axis=0)
        med = np.where(np.isfinite(med), med, 0.0)
        Xi = np.where(np.isfinite(X), X, med[None, :])
        Xi = np.column_stack([Xi, miss[:, mc].astype(float)])
        mu, sdv = Xi.mean(0), Xi.std(0)
        sdv[sdv == 0] = 1.0
        fit = dict(med=med, mu=mu, sd=sdv, mc=mc)
    else:
        Xi = np.where(np.isfinite(X), X, fit["med"][None, :])
        Xi = np.column_stack([Xi, miss[:, fit["mc"]].astype(float)])
    return (Xi - fit["mu"]) / fit["sd"], fit, names


def logit_fit(X, y, lam=LAM_LOGIT, it=30):
    n, p = X.shape
    Xb = np.column_stack([np.ones(n), X])
    beta = np.zeros(p + 1)
    R = lam * np.eye(p + 1)
    R[0, 0] = 0.0
    for _ in range(it):
        z = Xb @ beta
        pr = 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))
        wv = pr * (1 - pr) + 1e-9
        g = Xb.T @ (y - pr) - R @ beta
        Hm = (Xb * wv[:, None]).T @ Xb + R
        step = np.linalg.solve(Hm, g)
        beta += step
        if np.abs(step).max() < 1e-7:
            break
    return beta


def auc_cluster(score, y, cl, B=S.B_BOOT, seed=S.SEED, nb=NBINS):
    """AUC из счётчиков по корзинам квантилей балла (ничьи - половина), интервал - бутстреп по кластерам (активам)."""
    qs = np.unique(np.quantile(score, np.linspace(0, 1, nb + 1)[1:-1]))
    b = np.searchsorted(qs, score, side="right")
    nbin = len(qs) + 1
    ncl = int(cl.max()) + 1
    pos = np.zeros((ncl, nbin))
    neg = np.zeros((ncl, nbin))
    np.add.at(pos, (cl[y == 1], b[y == 1]), 1.0)
    np.add.at(neg, (cl[y == 0], b[y == 0]), 1.0)

    def auc_of(P, N):
        cn = np.cumsum(N, axis=-1) - N
        return (P * (cn + 0.5 * N)).sum(-1) / (P.sum(-1) * N.sum(-1))
    pt = float(auc_of(pos.sum(0), neg.sum(0)))
    res = []
    for w in S.boot_weights(ncl, B, seed):
        res.append(auc_of(w @ pos, w @ neg))
    res = np.concatenate(res)
    return pt, float(np.quantile(res, 0.05)), float(np.quantile(res, 0.95))


def predictive(tr, cuts, ui, outdir):
    rows = []
    c = {k: v[(tr["uni"] == ui) & (tr["code"] != CODE["END"])] for k, v in tr.items()}
    d = TY.derive(c)
    y = ((c["code"] == CODE["CV"]).astype(float))
    F = c["half"] == 0
    Wm = c["half"] == 1
    XF, fit, names = design({k: v[F] for k, v in d.items()})
    XW, _, _ = design({k: v[Wm] for k, v in d.items()}, fit)
    beta = logit_fit(XF, y[F])
    scoreW = np.column_stack([np.ones(len(XW)), XW]) @ beta
    scoreF = np.column_stack([np.ones(len(XF)), XF]) @ beta
    clW = S.cluster_ids(c["ai"][Wm])
    clF = S.cluster_ids(c["ai"][F])
    a_w = auc_cluster(scoreW, y[Wm], clW)
    a_f = auc_cluster(scoreF, y[F], clF)
    # net верхнего дециля предсказанной P(К), порог - по обучению (F)
    thr = np.quantile(scoreF, 0.9)
    top = scoreW >= thr
    netW = c["net"][Wm]
    for nm, msk in (("верхний дециль P(К) (порог по F)", top), ("нижний дециль P(К)", scoreW <= np.quantile(scoreF, 0.1)), ("все сделки W", np.ones(len(netW), bool))):
        cl = S.cluster_ids(c["ai"][Wm][msk]) if msk.any() else None
        if cl is None:
            continue
        pt, lo, hi, se, _ = S.ratio_boot(cl, netW[msk], np.ones(int(msk.sum())))
        qk = float(y[Wm][msk].mean())
        rows.append(dict(uni=ui, what=nm, n=int(msk.sum()), assets=int(cl.max()) + 1, qK=qk, net=float(pt[0]), net_lo=float(lo[0]), net_hi=float(hi[0]), auc_W=a_w[0], auc_W_lo=a_w[1], auc_W_hi=a_w[2],
                         auc_F=a_f[0]))
    # гребень по net
    ybet = c["net"][F]
    Xb = np.column_stack([np.ones(len(XF)), XF])
    R = LAM_RIDGE * np.eye(Xb.shape[1])
    R[0, 0] = 0.0
    bet = np.linalg.solve(Xb.T @ Xb + R, Xb.T @ ybet)
    pF = Xb @ bet
    pW = np.column_stack([np.ones(len(XW)), XW]) @ bet
    topr = pW >= np.quantile(pF, 0.9)
    cl = S.cluster_ids(c["ai"][Wm][topr]) if topr.any() else None
    if cl is not None:
        pt, lo, hi, se, _ = S.ratio_boot(cl, netW[topr], np.ones(int(topr.sum())))
        rows.append(dict(uni=ui, what="гребень по net: верхний дециль предсказанной net (порог по F)", n=int(topr.sum()), assets=int(cl.max()) + 1, qK=float(y[Wm][topr].mean()), net=float(pt[0]),
                         net_lo=float(lo[0]), net_hi=float(hi[0]), auc_W=a_w[0], auc_W_lo=a_w[1], auc_W_hi=a_w[2], auc_F=a_f[0]))
    coef = dict(zip(["const"] + names, beta[:len(names) + 1].tolist()))
    return rows, coef


def fund_predict(tr, outdir):
    """Предсказывает ли известная до входа ставка фандинга выплату за удержание? Ожидаемое = fund_day · hold / 1440 (bps); факт = fund сделки. Корреляция, наклон, средний факт при ожидаемо плохом / хорошем знаке; интервалы по активам."""
    rows = []
    for un, ui in (("V1", 2), ("V2", 3), ("B1", 0), ("B3", 1)):
        for hv in (0, 1, 2):
            m = (tr["uni"] == ui) & (tr["mtm"] == 0) & ((tr["half"] == hv) if hv < 2 else True)
            exp = tr["fund_day"][m] * tr["hold"][m] / 1440.0
            real = tr["fund"][m]
            ok = np.isfinite(exp)
            if ok.sum() < 30:
                continue
            exp, real, ai = exp[ok], real[ok], tr["ai"][m][ok]
            cl = S.cluster_ids(ai)
            row = dict(uni=un, half={0: "F", 1: "W", 2: "F+W"}[hv], n=int(ok.sum()), n_nonzero_real=int((real != 0).sum()))
            xc, yc = exp - exp.mean(), real - real.mean()
            row["corr"] = float((xc * yc).sum() / math.sqrt(max((xc * xc).sum() * (yc * yc).sum(), 1e-300)))
            row["slope"] = float((xc * yc).sum() / max((xc * xc).sum(), 1e-300))
            for nm, msk in (("exp<0", exp < 0), ("exp>=0", exp >= 0), ("exp<0 сильн.(нижн.терциль)", exp <= np.quantile(exp, 1 / 3))):
                if msk.sum() < 5:
                    continue
                pt, lo, hi, se, _ = S.ratio_boot(S.cluster_ids(ai[msk]), real[msk], np.ones(int(msk.sum())))
                row[nm + "_n"] = int(msk.sum())
                row[nm + "_fund"] = float(pt[0])
                row[nm + "_lo"] = float(lo[0])
                row[nm + "_hi"] = float(hi[0])
            rows.append(row)
    return rows


def write_rows(path, rows):
    cols = list(rows[0].keys()) if rows else []
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(cols)
        for r in rows:
            w.writerow([("%.6g" % r[c]) if isinstance(r[c], float) else r[c] for c in cols])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tables", required=True)
    ap.add_argument("--cuts", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    tr, ep, hints, act, meta = S.load_tables(a.tables)
    cuts = json.load(open(a.cuts, encoding="utf-8"))
    os.makedirs(a.out, exist_ok=True)
    rows = []
    tr = dict(tr)
    # признак «шорт-нога на Bybit» (дорогая нога на Bybit)
    dd = TY.derive({k: v for k, v in tr.items()})
    tr["byb_short_f"] = dd["byb_short"].astype(float)
    for un, ui, csets in (("V1", 2, (("S1+S2+S3", [2, 3, 4]), ("S1", [2]), ("S3", [4]))), ("V2", 3, (("S1+S2+S3", [2, 3, 4]), ("S1", [2]), ("TO", [6]))),
                           ("B1", 0, (("S0+S2+S3", [1, 3, 4]),)), ("B3", 1, (("S0+S2+S3", [1, 3, 4]), ("S0", [1])))):
        for cname, codes in csets:
            for hv in (0, 1):
                for flag in ("byb", "byb_short_f"):
                    r = stratified_bybit_test(tr, ui, hv, codes, flag)
                    r["name"] = un
                    r["cset"] = cname
                    ns, na, ne = independent_episodes(tr, ui, hv, codes)
                    r.update(stop_trades=ns, stop_assets_n=na, stop_episodes=ne)
                    rows.append(r)
    write_rows(os.path.join(a.out, "E_bybit_stops.csv"), rows)
    allp, coefs = [], {}
    for un, ui in (("B1", 0), ("B3", 1)):
        pr, coef = predictive(tr, cuts, ui, a.out)
        for r in pr:
            r["uni"] = un
        allp += pr
        coefs[un] = coef
    write_rows(os.path.join(a.out, "E_predictive.csv"), allp)
    fp = fund_predict(tr, a.out)
    allcols = sorted({k for r in fp for k in r}, key=lambda k: (k not in ("uni", "half", "n"), k))
    for r in fp:
        for k in allcols:
            r.setdefault(k, None)
    with open(os.path.join(a.out, "E_fund_predict.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(allcols)
        for r in fp:
            w.writerow(["" if r[c] is None else (("%.6g" % r[c]) if isinstance(r[c], float) else r[c]) for c in allcols])
    json.dump(coefs, open(os.path.join(a.out, "E_logit_coef.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
