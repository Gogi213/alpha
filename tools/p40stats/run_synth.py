"""П-40 r2 этап 1: проверка статистики p40stats на синтетике с известным ответом (реальные данные не читаются).
(а) нуль — ложные срабатывания ≈ уровню; (б) посаженный эффект — мощность; (в) кластерная зависимость — покрытие интервалов.
Запуск: python tools/p40stats/run_synth.py [out.json]   (один процесс, лёгкий)"""
import json
import sys
import time

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


def gen(rng, A=100, D=14, lam=3.0, p0=0.5, scen="both", delta=0.0, lam_scale=1.0):
    tau_a, tau_d, tau_ad, sh_a, sh_d = SCEN[scen]
    n = rng.poisson(lam * lam_scale, (A, D))
    a = np.repeat(np.repeat(np.arange(A), D), n.ravel())
    d = np.repeat(np.tile(np.arange(D), A), n.ravel())
    m = len(a)
    t = dict(asset=a, day=d, pair=np.zeros(m, int), net_bps=rng.normal(0, 20, m))
    for k, name in enumerate(KINDS):
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


def run_a_c(n_sim, B, A, D):
    """(а)+(в): нуль в 4 режимах зависимости; покрытие 90 %-го интервала и доля p<0,10 для схем бутстрепа."""
    methods = [("iid", None, "pct"), ("cluster", "cluster", "t"), ("day/t", "day", "t"), ("day/pct", "day", "pct"),
               ("asset/t", "asset", "t"), ("twoway/t", "twoway", "t")]
    res = {}
    for scen in SCEN:
        rng = np.random.default_rng(P.SEED + list(SCEN).index(scen))
        miss = {m[0]: 0 for m in methods}
        pfp = {m[0]: 0 for m in methods}
        wid = {m[0]: [] for m in methods}
        hour_naive = hour_rs = 0
        for _ in range(n_sim):
            t = gen(rng, A, D, scen=scen)
            S = P.cell_stats(t, "vol5m", "conv_H", n_a=A, n_d=D)
            for name, sch, ci in methods:
                r = P.contrast_iid(t, "vol5m", "conv_H", B=B, rng=rng) if sch is None else P.contrast(S, sch, B, ci, rng)
                miss[name] += not (r["lo"] <= 0 <= r["hi"])
                pfp[name] += (r["p"] < 0.10)
                wid[name].append(r["hi"] - r["lo"])
            h = P.chi2_homog(t, t["f_hour"], "conv_H", B=B, rng=rng)
            hour_naive += h["p_naive"] < 0.05
            hour_rs += h["p_rs"] < 0.05
        res[scen] = dict(
            ci_miss={k: v / n_sim for k, v in miss.items()}, p_lt_010={k: v / n_sim for k, v in pfp.items()},
            ci_width={k: float(np.mean(v)) for k, v in wid.items()}, hour_chi2_fp_naive=hour_naive / n_sim,
            hour_chi2_fp_rs=hour_rs / n_sim)
    return res


def run_family(n_sim, B, A, D, scen="both"):
    """(а) семейство 12 признаков × 2 исхода (24 испытания) под глобальным нулём: BH q=0,10 — доля наборов с ≥1 прохождением."""
    rng = np.random.default_rng(P.SEED + 7)
    any_rej = 0
    nrej = []
    for _ in range(n_sim):
        t = gen(rng, A, D, scen=scen)
        ps = []
        for f, kind in P.FEATURES.items():
            for o in P.OUTCOMES:
                order = P.hour_order(t["f_hour"], t[o]) if kind == "hour" else None
                ps.append(P.contrast(P.cell_stats(t, f, o, order, A, D), "day", B, "t", rng)["p"])
        r = P.bh(ps)
        any_rej += r.any()
        nrej.append(int(r.sum()))
    return dict(tests=24, sets_with_rejection=any_rej / n_sim, mean_rejections=float(np.mean(nrej)))


def run_power(n_sim, B, A, D, scen="both", ks=(0.5, 1.0, 1.5)):
    """(б) посаженный эффект на признаке depth (верхний терциль) в исходе conv_H: мощность при эффекте k × MDE."""
    rng = np.random.default_rng(P.SEED + 11)
    t0 = gen(rng, A, D, scen=scen)
    S0 = P.cell_stats(t0, "depth", "conv_H", None, A, D)
    p = float(t0["conv_H"].mean())
    deff, rho = P.deff_icc(t0["asset"] * D + t0["day"], t0["conv_H"].astype(float))
    n_hi, n_lo = S0[:, :, 0].sum(), S0[:, :, 2].sum()
    mde_ex = float(P.mde_prop_exact(p, deff, n_hi, n_lo))
    mde_pr = float(P.mde_prop_protocol(p, deff, len(t0["asset"])))
    out = dict(p=p, deff=deff, rho=rho, n_hi=int(n_hi), n_lo=int(n_lo), mde_protocol_formula=mde_pr, mde_exact=mde_ex, power={})
    for label, eff in [("k=0", 0.0)] + [(f"k={k}·MDE_exact", k * mde_ex) for k in ks] + [("1·MDE_protocol", mde_pr)]:
        delta = delta_for(eff, scen) if eff > 0 else 0.0
        ok_day = ok_cl = 0
        eff_hat = []
        for _ in range(n_sim):
            t = gen(rng, A, D, scen=scen, delta=delta)
            S = P.cell_stats(t, "depth", "conv_H", None, A, D)
            eff_hat.append(float(P.effect_of(S.reshape(-1, 4).sum(0))))
            ok_day += P.contrast(S, "day", B, "t", rng)["lo"] > 0
            ok_cl += P.contrast(S, "cluster", B, "t", rng)["lo"] > 0
        out["power"][label] = dict(planted=eff, true_check=true_effect(delta, scen) if delta else 0.0, mean_est=float(np.mean(eff_hat)),
                                   reject_day=ok_day / n_sim, reject_cluster=ok_cl / n_sim)
    return out


def run_candidate(n_sim, B, A, D1=14, D2=22, scen="both", eff=None):
    """Правило кандидата §3.5 (п. 1–4) на синтетике: нуль — доля «кандидатов»; посаженный эффект — доля находок."""
    rng = np.random.default_rng(P.SEED + 13)
    delta = delta_for(eff, scen) if eff else 0.0
    cand = 0
    for _ in range(n_sim):
        t1, t2 = gen(rng, A, D1, scen=scen, delta=delta), gen(rng, A, D2, scen=scen, delta=delta)
        S1, S2 = P.cell_stats(t1, "depth", "conv_H", None, A, D1), P.cell_stats(t2, "depth", "conv_H", None, A, D2)
        if not (P.strata_ok(S1) and P.strata_ok(S2)):
            continue
        pool = np.concatenate([S1, S2], 1)
        ps = [P.contrast(pool, "day", B, "t", rng)["p"]]
        for f in KINDS[1:]:   # остальные 11 признаков — пустое семейство ради честного m для BH (24 испытания в бюджете)
            for o in P.OUTCOMES:
                order = P.hour_order(t1["f_hour"], t1[o]) if f == "hour" else None
                ps.append(P.contrast(P.cell_stats(t1, f, o, order, A, D1), "day", B, "t", rng)["p"])
        P.budget_check(len(ps))
        passed = P.bh(ps)[0]
        c1 = P.contrast(S1, "day", B, "t", rng)
        c2 = P.contrast(S2, "day", B, "t", rng)
        loo = P.leave_top_k(S1, 3, B, rng)
        ctrl = [P.contrast(P.cell_stats(gen(rng, A, D1, scen=scen), "depth", "conv_H", None, A, D1), "day", B, "t", rng)
                for _ in range(3)]    # контроли: таблицы без эффекта
        cand += P.candidate(c1, c2, passed, loo, ctrl)["candidate"]
    return dict(n_sim=n_sim, planted=eff, candidate_rate=cand / n_sim)


if __name__ == "__main__":
    t0 = time.time()
    A, D = 100, 14
    out = dict(config=dict(A=A, D=D, seed=P.SEED, scenarios=SCEN))
    out["null_and_clusters"] = run_a_c(n_sim=150, B=400, A=A, D=D)
    print("a/v", round(time.time() - t0), "s", flush=True)
    out["null_D22"] = run_a_c(n_sim=60, B=400, A=A, D=22)
    print("a/v D22", round(time.time() - t0), "s", flush=True)
    out["family_bh"] = run_family(n_sim=150, B=400, A=A, D=D)
    print("family", round(time.time() - t0), "s", flush=True)
    out["power"] = run_power(n_sim=150, B=400, A=A, D=D)
    print("power", round(time.time() - t0), "s", flush=True)
    mde_ex = out["power"]["mde_exact"]
    out["candidate_null"] = run_candidate(40, 300, A, eff=None)
    out["candidate_planted"] = run_candidate(40, 300, A, eff=2 * mde_ex)
    out["seconds"] = round(time.time() - t0)
    json.dump(out, open(sys.argv[1] if len(sys.argv) > 1 else "p40stats-synth.json", "w"), indent=1, ensure_ascii=False)
    print(json.dumps(out, indent=1, ensure_ascii=False))
