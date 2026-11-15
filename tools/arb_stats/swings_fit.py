"""Подгонка процесса возврата к среднему (OU / AR(1)) к середине исполнимых цен пары, проверка подгонки, прогноз частоты качелей и её сверка с измеренной.

Запуск: python3 swings_fit.py <swings.pkl> <выход fit.pkl>
Для каждой пары: минутная сетка m (swings.pair_swings): μ, σ, AR(1) φ → период полураспада; остатки — Льюнга–Бокс (10 лагов); AR(2) против AR(1) по BIC;
полураспад по автокорреляции 1 с-сетки на масштабах 1 с … 1 ч (у OU — один и тот же); подгонка одной и двух экспонент к автокорреляции на 16 лагах 1 с … 6 ч;
частота качелей ±N по OU: 2 / (T_вверх + T_вниз) — средние времена первого прохода между барьерами U = N + h и −N − h (h — медианный полуспред, середина m).
"""
import pickle
import sys
from pathlib import Path

import numpy as np
from scipy import stats
from scipy.optimize import least_squares
from scipy.special import log_ndtr

sys.path.insert(0, str(Path(__file__).parent))
import swings  # noqa: E402

HOUR = swings.HOUR


def ou_swing_rate(mu, sig, theta, U, Lw):
    """Качелей в час для OU (θ — 1/с): барьеры середины U (верхний) и Lw (нижний); 2 / (T_вверх(Lw→U) + T_вниз(U→Lw))."""
    if not (sig > 0 and theta > 0 and U > Lw):
        return float("nan")
    zU, zL = (U - mu) / sig, (Lw - mu) / sig
    if max(abs(zU), abs(zL)) > 30:
        return 0.0
    u = np.linspace(zL, zU, 4001)
    up = np.trapezoid(np.exp(0.5 * u * u + log_ndtr(u)), u)
    dn = np.trapezoid(np.exp(0.5 * u * u + log_ndtr(-u)), u)
    return 3600.0 * 2.0 / (np.sqrt(2 * np.pi) / theta * (up + dn))


def lb_p(res, lags=10, k=1):
    """Льюнг–Бокс: p-значение по остаткам (k — число подогнанных параметров авторегрессии)."""
    n = len(res)
    r = res - res.mean()
    d = (r * r).sum()
    ac = np.array([(r[l:] * r[:-l]).sum() / d for l in range(1, lags + 1)])
    q = n * (n + 2) * (ac ** 2 / (n - np.arange(1, lags + 1))).sum()
    return float(stats.chi2.sf(q, lags - k))


def ar_fit(x, p):
    """МНК AR(p) с константой. -> (φ[1..p], остатки, BIC)."""
    y = x[p:]
    Z = np.column_stack([np.ones(len(y))] + [x[p - k:len(x) - k] for k in range(1, p + 1)])
    b, *_ = np.linalg.lstsq(Z, y, rcond=None)
    res = y - Z @ b
    n = len(y)
    return b[1:], res, n * np.log(max((res ** 2).mean(), 1e-300)) + (p + 1) * np.log(n)


def fit_acf(lags, rho):
    """Одна экспонента a·e^{−τ/T} и две a1·e^{−τ/T1} + a2·e^{−τ/T2} (a1 + a2 ≤ 1) по автокорреляции. -> (sse1, T, sse2, T1, T2, a1, a2)."""
    tau = np.asarray(lags, float)

    def m1(p):
        return p[0] * np.exp(-tau / np.exp(p[1]))

    def m2(p):
        return p[0] * np.exp(-tau / np.exp(p[1])) + p[2] * np.exp(-tau / np.exp(p[3]))

    best1 = min((least_squares(lambda p: m1(p) - rho, [0.8, np.log(t)], bounds=([0, np.log(0.1)], [1, np.log(1e6)])) for t in (10, 300, 5000)), key=lambda r: r.cost)
    best2 = None
    for t1 in (3, 30):
        for t2 in (600, 7200):
            r = least_squares(lambda p: m2(p) - rho, [0.4, np.log(t1), 0.4, np.log(t2)], bounds=([0, np.log(0.1), 0, np.log(0.1)], [1, np.log(1e6), 1, np.log(1e6)]),
                              )
            if r.x[0] + r.x[2] <= 1.0001 and (best2 is None or r.cost < best2.cost):
                best2 = r
    if best2 is None:
        best2 = best1
        p2 = [best1.x[0], best1.x[1], 0.0, best1.x[1]]
    else:
        p2 = best2.x
    o = np.argsort([np.exp(p2[1]), np.exp(p2[3])])
    T = [np.exp(p2[1]), np.exp(p2[3])]
    A = [p2[0], p2[2]]
    return (2 * best1.cost, float(np.exp(best1.x[1])), 2 * best2.cost, T[o[0]], T[o[1]], A[o[0]], A[o[1]])


def hl_from_rho(rho, step_s):
    return step_s * np.log(2) / -np.log(rho) if 0 < rho < 1 else float("nan")


def fit_pair(v, t0, t1):
    x = v["mm"].astype(float)
    x = x[~np.isnan(x)]
    out = {"nmin": len(x)}
    hs = float(np.nanmedian(v["hm"]))
    out["hs"] = hs
    out["hours"] = float(v["cov"].sum() / HOUR)
    if len(x) < 400:
        return out
    (phi1,), res1, bic1 = ar_fit(x, 1)
    (p21, p22), res2, bic2 = ar_fit(x, 2)
    mu, sd = float(x.mean()), float(x.std())
    out.update({"mu": mu, "sd": sd, "phi1": float(phi1), "lb1": lb_p(res1, 10, 1), "lb2": lb_p(res2, 10, 2), "dbic": float(bic1 - bic2),
                "hl_min": float(hl_from_rho(phi1, 60) / 60) if phi1 > 0 else float("nan")})
    if v.get("acf") is not None:
        rho = np.asarray(v["acf"], float)
        lg = dict(zip(swings.LAGS_S, rho))
        out["hl_acf"] = {d: hl_from_rho(lg[d], d) for d in (1, 10, 60, 600, 3600)}
        out["acf"] = rho
        try:
            out["two"] = fit_acf(swings.LAGS_S, rho)
        except Exception:  # noqa: BLE001
            out["two"] = None
    th = -np.log(min(max(phi1, 0.02), 0.9995)) / 60.0
    out["ou"] = {iN: ou_swing_rate(mu, sd, th, N + hs, -N - hs) for iN, N in enumerate(swings.NS)}
    out["rate"], out["cv"], out["nsw"] = {}, {}, {}
    for key, (tc, dur, gross, side) in v["sw"].items():
        out["nsw"][key] = len(tc)
        out["rate"][key] = len(tc) / out["hours"] if out["hours"] > 0 else float("nan")
        iv = np.diff(np.sort(tc))
        out["cv"][key] = float(iv.std() / iv.mean()) if len(iv) >= 10 and iv.mean() > 0 else float("nan")
    return out


def main(src, dst):
    d = pickle.load(open(src, "rb"))
    res = {}
    for k, v in d["pairs"].items():
        res[k] = fit_pair(v, d["t0"], d["t1"])
        res[k]["grp"] = "bx" if "bingx" in k[1:] else "ok"
    with open(dst, "wb") as f:
        pickle.dump({"fit": res, "t0": d["t0"], "t1": d["t1"], "excluded": d["excluded"], "ns": d["ns"], "ls": d["ls"], "lags_s": d["lags_s"]}, f, protocol=4)
    print({"pairs": len(res)})


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
