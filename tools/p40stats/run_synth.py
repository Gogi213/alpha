"""П-40 r2 этап 1: проверка статистики p40stats на синтетике с известным ответом (реальные данные не читаются).
Этап 1 (stage1): нуль в 4 режимах зависимости, покрытие и мощность 6 вариантов интервала (выбор схемы, В-170).
Этап 2 (stage2 <схема>): семейство BH (час по χ²), правило кандидата §3.5, H-Bybit — на выбранной схеме.
Запуск: python run_synth.py stage1 out1.json [n_sim] ; python run_synth.py stage2 out2.json <схема> [n_sim]  (процессов: 8)"""
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np
from scipy.special import expit, logit

import p40stats as P

KINDS = list(P.FEATURES)
SCEN = {  # tau_a, tau_d, tau_ad (логит-sd исхода), sh_a, sh_d (доли признака, постоянные по активу / суткам)
    "iid": (0.0, 0.0, 0.0, 0.0, 0.0),
    "asset": (1.0, 0.0, 0.3, 0.8, 0.0),
    "day": (0.0, 0.7, 0.3, 0.0, 0.8),
    "both": (0.8, 0.6, 0.3, 0.5, 0.4),
}
METHODS = [("iid", None, "pct"), ("cluster", "cluster", "t"), ("day/t", "day", "t"), ("asset/t", "asset", "t"),
           ("twoway/t", "twoway", "t"), ("cgm", "cgm", "t"), ("twoway_sub", "twoway_sub", "t")]
WORKERS = 8


def gen(rng, A=100, D=14, lam=3.0, p0=0.5, scen="both", delta=0.0, hdelta=0.0):
    tau_a, tau_d, tau_ad, sh_a, sh_d = SCEN[scen]
    n = rng.poisson(lam, (A, D))
    a = np.repeat(np.repeat(np.arange(A), D), n.ravel())
    d = np.repeat(np.tile(np.arange(D), A), n.ravel())
    m = len(a)
    t = dict(asset=a, day=d, pair=np.zeros(m, int), net_bps=rng.normal(0, 20, m))
    for name in KINDS:
        za, zd = rng.normal(size=A), rng.normal(size=D)
        z = np.sqrt(sh_a) * za[a] + np.sqrt(sh_d) * zd[d] + np.sqrt(1 - sh_a - sh_d) * rng.normal(size=m)
        kind = P.FEATURES[name]
        if kind == "ord":
            f = np.digitize(z, np.quantile(z, [1 / 3, 2 / 3]))
        elif kind == "bin":
            f = (z > np.median(z)).astype(int)
        else:
            f = np.digitize(z, np.quantile(z, [.25, .5, .75]))
        t["f_" + name] = f
    bl = rng.random(A) < 0.3                      # нога Bybit — свойство актива (пары)
    t["bybit_leg"] = bl[a].astype(int)
    rate_log = tau_a * rng.normal(size=A)[a] + tau_d * rng.normal(size=D)[d] + tau_ad * rng.normal(size=(A, D))[a, d]
    tt = 20.0 * np.exp(rate_log + hdelta * t["bybit_leg"]) * rng.exponential(1.0, m) / np.log(2)   # медиана ≈ 20·e^{…} с
    t["cens"] = (tt > 60.0).astype(int)
    t["t_conv_s"] = np.minimum(tt, 60.0)
    for o, pbase in zip(P.OUTCOMES, (p0, 0.3)):
        b = tau_a * rng.normal(size=A)[a] + tau_d * rng.normal(size=D)[d] + tau_ad * rng.normal(size=(A, D))[a, d]
        lg = logit(pbase) + b + (delta * (t["f_depth"] == 2) if o == "conv_H" else 0.0)
        t[o] = (rng.random(m) < expit(lg)).astype(int)
    return t


def true_effect(delta, scen, p0=0.5, n=400000, seed=1):
    tau_a, tau_d, tau_ad, *_ = SCEN[scen]
    b = np.sqrt(tau_a ** 2 + tau_d ** 2 + tau_ad ** 2) * np.random.default_rng(seed).normal(size=n)
    return float(expit(logit(p0) + b + delta).mean() - expit(logit(p0) + b).mean())


def delta_for(eff, scen):
    lo, hi = 0.0, 4.0
    for _ in range(40):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if true_effect(mid, scen) < eff else (lo, mid)
    return (lo + hi) / 2


def pmap(fn, args):
    with ProcessPoolExecutor(WORKERS) as ex:
        return list(ex.map(fn, args))


def chunks(n_sim):
    return [n_sim // WORKERS + (i < n_sim % WORKERS) for i in range(WORKERS)]


# ---------- этап 1: покрытие под нулём, мощность ----------
def _null_chunk(a):
    scen, A, D, n, seed, B = a
    rng = np.random.default_rng(seed)
    r = {m[0]: dict(miss=[], pfp=[], wid=[], se=[]) for m in METHODS}
    hr = dict(naive=[], day=[], cgm=[])
    for _ in range(n):
        t = gen(rng, A, D, scen=scen)
        S = P.cell_stats(t, "vol5m", "conv_H", n_a=A, n_d=D)
        for name, sch, ci in METHODS:
            x = P.contrast_iid(t, "vol5m", "conv_H", B=B, rng=rng) if sch is None else P.contrast(S, sch, B, ci, rng)
            r[name]["miss"].append(not (x["lo"] <= 0 <= x["hi"]))
            r[name]["pfp"].append(x["p"] < 0.10)
            r[name]["wid"].append(x["hi"] - x["lo"])
            r[name]["se"].append(x["se"])
        for sch in ("day", "cgm"):
            h = P.chi2_homog(t, t["f_hour"], "conv_H", sch, A, D)
            hr[sch].append(h["p_rs"] < 0.05)
            if sch == "day":
                hr["naive"].append(h["p_naive"] < 0.05)
    return r, hr


def run_null(scen, A, D, n_sim, B):
    out = pmap(_null_chunk, [(scen, A, D, n, P.SEED * 1000 + list(SCEN).index(scen) * 100 + i, B) for i, n in enumerate(chunks(n_sim))])
    res = dict(ci_miss={}, p_lt_010={}, ci_width={}, se_mean={})
    for name, *_ in METHODS:
        for key, f in (("ci_miss", "miss"), ("p_lt_010", "pfp"), ("ci_width", "wid"), ("se_mean", "se")):
            res[key][name] = float(np.mean(sum((o[0][name][f] for o in out), [])))
    res["hour_chi2_fp"] = {k: float(np.mean(sum((o[1][k] for o in out), []))) for k in ("naive", "day", "cgm")}
    return res


def _power_chunk(a):
    scen, A, D, n, seed, B, delta = a
    rng = np.random.default_rng(seed)
    rej = {m[0]: 0 for m in METHODS if m[1]}
    est = []
    for _ in range(n):
        t = gen(rng, A, D, scen=scen, delta=delta)
        S = P.cell_stats(t, "depth", "conv_H", None, A, D)
        est.append(float(P.effect_of(S.reshape(-1, 4).sum(0))))
        for name, sch, ci in METHODS:
            if sch:
                rej[name] += P.contrast(S, sch, B, ci, rng)["lo"] > 0
    return rej, est


def run_power(scen, A, D, n_sim, B, se_cgm, ks=(0.0, 1.0, 1.5, 2.0)):
    """Посаженный эффект на depth (верхний терциль) → conv_H; эффект = k × MDE_ref, MDE_ref = 2,49·SE_cgm (нуль, 'both')."""
    mde_ref = P.mde_se(se_cgm)
    out = dict(mde_ref_cgm=mde_ref, power={})
    for k in ks:
        eff = k * mde_ref
        delta = delta_for(eff, scen) if eff > 0 else 0.0
        res = pmap(_power_chunk, [(scen, A, D, n, P.SEED * 7 + i + int(k * 10) * 31, B, delta) for i, n in enumerate(chunks(n_sim))])
        out["power"][f"k={k}"] = dict(
            planted=eff, true_check=true_effect(delta, scen) if delta else 0.0, mean_est=float(np.mean(sum((r[1] for r in res), []))),
            reject={m: float(sum(r[0][m] for r in res) / n_sim) for m in res[0][0]})
    return out


# ---------- этап 2: выбранная схема ----------
def _pooled(t1, t2, D1):
    t = {k: np.concatenate([t1[k], t2[k]]) for k in t1}
    t["day"] = np.concatenate([t1["day"], t2["day"] + D1])
    return t


def _p_all(t, A, ND, scheme, B, rng):
    """24 p на данных t (12 признаков × 2 исхода); час — χ²; индекс 0 = depth/conv_H."""
    ps = []
    for f, kind in P.FEATURES.items():
        for o in P.OUTCOMES:
            ps.append(P.hour_trial(t, o, scheme, A, ND) if kind == "hour" else
                      P.contrast(P.cell_stats(t, f, o, None, A, ND), scheme, B, "t", rng)["p"])
    return ps


def _family_chunk(a):
    scen, A, D, n, seed, B, scheme = a
    rng = np.random.default_rng(seed)
    rej = []
    for _ in range(n):
        rej.append(int(P.bh(_p_all(gen(rng, A, D, scen=scen), A, D, scheme, B, rng)).sum()))
    return rej


def _cand_chunk(a):
    scen, A, D1, D2, n, seed, B, scheme, delta, target = a
    rng = np.random.default_rng(seed)
    cand = bhp = c1n = 0
    skipped = 0
    for _ in range(n):
        t1, t2 = gen(rng, A, D1, scen=scen, delta=delta), gen(rng, A, D2, scen=scen, delta=delta)
        tp = _pooled(t1, t2, D1)
        ND = D1 + D2
        if target == "depth":
            S1 = P.cell_stats(t1, "depth", "conv_H", None, A, D1)
            S2 = P.cell_stats(t2, "depth", "conv_H", None, A, D2)
            order = None
        else:
            order = P.hour_order(t1["f_hour"], t1["conv_H"])
            S1 = P.cell_stats(t1, "hour", "conv_H", order, A, D1)
            S2 = P.cell_stats(t2, "hour", "conv_H", order, A, D2)
        if not (P.strata_ok(S1) and P.strata_ok(S2)):
            skipped += 1
            continue
        ps = _p_all(tp, A, ND, scheme, B, rng)    # все 24 p — на D1∪D2
        P.budget_check(len(ps))
        idx = 0 if target == "depth" else 2 * KINDS.index("hour")
        passed = bool(P.bh(ps)[idx])
        c1, c2 = P.contrast(S1, scheme, B, "t", rng), P.contrast(S2, scheme, B, "t", rng)
        loo = P.leave_top_k(S1, 3, B, rng, scheme=scheme)
        ctrl = []
        for _c in range(3):
            tc = gen(rng, A, D1, scen=scen)
            if target == "depth":
                Sc = P.cell_stats(tc, "depth", "conv_H", None, A, D1)
            else:
                Sc = P.cell_stats(tc, "hour", "conv_H", order, A, D1)
            ctrl.append(P.contrast(Sc, scheme, B, "t", rng))
        r = P.candidate(c1, c2, passed, loo, ctrl)
        cand += r["candidate"]
        bhp += passed
        c1n += r["c1"]
    return cand, bhp, c1n, skipped


def _hb_chunk(a):
    scen, A, D, n, seed, B, scheme, hdelta = a
    rng = np.random.default_rng(seed)
    miss = rej = 0
    ok = 0
    est = []
    for _ in range(n):
        t = gen(rng, A, D, scen=scen, hdelta=hdelta)
        r = P.h_bybit(t, scheme, B, rng)
        if not np.isfinite(r["median"]["effect"]):
            continue
        ok += 1
        est.append(r["median"]["effect"])
        miss += not (r["median"]["lo"] <= 0 <= r["median"]["hi"])
        rej += r["median"]["lo"] > 0 or r["median"]["hi"] < 0
    return ok, miss, rej, est


def stage2(scheme, n_sim, out):
    A, D, B = 100, 14, 300
    res = dict(scheme=scheme, n_sim=n_sim)
    t0 = time.time()
    rej = sum(pmap(_family_chunk, [("both", A, D, n, P.SEED + 70 + i, B, scheme) for i, n in enumerate(chunks(n_sim))]), [])
    res["family_bh"] = dict(tests=24, sets_with_rejection=float(np.mean(np.array(rej) > 0)), mean_rejections=float(np.mean(rej)))
    print("family", round(time.time() - t0), flush=True)
    se = res.get("se")
    # посаженный эффект для кандидата: 2× MDE_ref (по SE схемы на нуле)
    S0 = [P.contrast(P.cell_stats(gen(np.random.default_rng(i), A, D, scen="both"), "depth", "conv_H", None, A, D), scheme, B, "t",
                     np.random.default_rng(i))["se"] for i in range(30)]
    mde = P.mde_se(float(np.mean(S0)))
    res["mde_ref"] = mde
    for label, target, eff in (("null_depth", "depth", None), ("null_hour", "hour", None), ("planted_2mde_depth", "depth", 2 * mde),
                               ("planted_3mde_depth", "depth", 3 * mde)):
        delta = delta_for(eff, "both") if eff else 0.0
        o = pmap(_cand_chunk, [("both", A, 14, 22, n, P.SEED + 90 + i, B, scheme, delta, target) for i, n in enumerate(chunks(n_sim))])
        tot = np.sum(o, 0)
        used = max(n_sim - tot[3], 1)
        res["candidate_" + label] = dict(planted=eff, candidate_rate=float(tot[0] / used), bh_pass_rate=float(tot[1] / used),
                                         c1_rate=float(tot[2] / used), skipped=int(tot[3]))
        print(label, round(time.time() - t0), flush=True)
    for label, hd in (("null", 0.0), ("planted_ln1.5", np.log(1.5)), ("planted_ln2", np.log(2.0))):
        o = pmap(_hb_chunk, [("both", A, D, n, P.SEED + 110 + i, B, "twoway_sub" if scheme == "cgm" else scheme, hd)
                             for i, n in enumerate(chunks(n_sim))])
        ok = sum(x[0] for x in o)
        res["hbybit_" + label] = dict(n_ok=ok, ci_miss=sum(x[1] for x in o) / max(ok, 1), reject=sum(x[2] for x in o) / max(ok, 1),
                                      mean_diff_s=float(np.mean(sum((x[3] for x in o), []))))
        print("hbybit", label, round(time.time() - t0), flush=True)
    res["seconds"] = round(time.time() - t0)
    json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print(json.dumps(res, indent=1, ensure_ascii=False))


def stage1(n_sim, out):
    A, B = 100, 400
    res = dict(config=dict(A=A, seed=P.SEED, n_sim=n_sim, scenarios=SCEN, B=B))
    t0 = time.time()
    for D in (14, 22):
        res[f"null_D{D}"] = {}
        for scen in SCEN:
            res[f"null_D{D}"][scen] = run_null(scen, A, D, n_sim, B)
            print("null", D, scen, round(time.time() - t0), flush=True)
    se_cgm = res["null_D14"]["both"]["se_mean"]["cgm"]
    res["power_both_D14"] = run_power("both", A, 14, n_sim, B, se_cgm)
    print("power", round(time.time() - t0), flush=True)
    res["seconds"] = round(time.time() - t0)
    json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print(json.dumps(res, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    if sys.argv[1] == "stage1":
        stage1(int(sys.argv[3]) if len(sys.argv) > 3 else 400, sys.argv[2])
    else:
        stage2(sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else 200, sys.argv[2])
