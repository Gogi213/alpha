#!/usr/bin/env python3
"""TK-493 w3 (M-04, M-06, M-11): тест размера интервалов по суткам на синтетическом нуле + мощность + n_эфф.

Что проверяет. У нижней границы 90 %-интервала (односторонний уровень 5 %) на синтетическом нуле (среднее 0) доля случаев
«нижняя граница > 0» должна быть ≈ 5 %. Меряется для n = 15 и 21 суток, блок 3, на сценариях нуля:
  normal  - независимые нормальные сутки;            t3     - независимые t(3), масштаб на σ = 1;
  sparse  - 35 % суток с исходом N(0,1), остальные 0;  ar1    - AR(1) с φ = 0,3 (чувствительность: φ - допущение аудитора, данными не подтверждена).
Способы: percentile - перцентильный блочный бутстреп (то, что считает `portfolio.block_bootstrap`; здесь векторно, тот же алгоритм:
скользящие блоки с оборачиванием); boot_t - студентизированный блочный бутстреп-t (se по блокам ресэмпла и по блокам исходного ряда);
t_block - `portfolio.block_t_interval` (t-интервал по блокам, df = n_эфф−1; сам код боевой функции); t_iid - справка (t по суткам, df = n−1).

Мощность. Все способы сдвиг-эквивариантны (нижняя граница ряда x+δ равна нижней границе x плюс δ), поэтому мощность при сдвиге δ
считается по нулевым границам: P(нижняя > 0 | δ) = P(нижняя_нуль > −δ); `delta80` - наименьший сдвиг (в σ суток) с мощностью 80 %
(эмпирический МРЭ правила «нижняя граница > 0»).

Условие (б) w3 M-06: разность «клетка − контроль» по суткам в паре; контроль - независимая копия сценария (rho = 0) либо с
корреляцией rho (нормальные сутки). Совместный размер и мощность правила (а)&(б′) - файл -joint.csv.

n_эфф (M-11): ⌈n/3⌉ против `effective_n_from_autocorr` (навык alpha-research) на сценариях normal и ar1; относительная ошибка σ по m = 7 суткам.

Запуск: python size_test.py --reps 4000 --boot 3000 --seed 37 --out-prefix docs/findings/xdl9-stats-fixes-2026-11-06
Только numpy и stdlib. Число повторов (--reps), B (--boot) и зерно (--seed) - обязательные аргументы без значений по умолчанию.
"""
import argparse
import csv
import importlib.util
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


P = _load("statarb_portfolio_size_test", os.path.join(HERE, "portfolio.py"))

BLOCK = 3
SIGMA = {"normal": 1.0, "t3": 1.0, "sparse": math.sqrt(0.35), "ar1": 1.0 / math.sqrt(1 - 0.3 * 0.3)}
SCENARIOS = ("normal", "t3", "sparse", "ar1")
SIZES = (15, 21)
METHODS = ("percentile", "boot_t", "t_block", "t_iid")


def gen(rng, scen, reps, n):
    """reps рядов длины n со средним 0 и σ = SIGMA[scen]."""
    if scen == "normal":
        return rng.normal(size=(reps, n))
    if scen == "t3":
        return rng.standard_t(3, size=(reps, n)) / math.sqrt(3.0)
    if scen == "sparse":
        return rng.normal(size=(reps, n)) * (rng.random((reps, n)) < 0.35)
    if scen == "ar1":
        e = rng.normal(size=(reps, n))
        x = np.empty_like(e)
        x[:, 0] = e[:, 0] * SIGMA["ar1"]
        for i in range(1, n):
            x[:, i] = 0.3 * x[:, i - 1] + e[:, i]
        return x
    raise ValueError(scen)


def _resample(x, n_boot, rng, block):
    n = len(x)
    nb = math.ceil(n / block)
    st = rng.integers(0, n, size=(n_boot, nb, 1))
    idx = ((st + np.arange(block)) % n).reshape(n_boot, nb * block)[:, :n]
    return x[idx], nb


def _batch_se(y, block):
    """se среднего по блокам подряд (формула block_t_interval) для каждой строки y (последний блок может быть короче)."""
    n = y.shape[-1]
    nb = math.ceil(n / block)
    mean = y.mean(-1)
    ss = 0.0
    for k in range(nb):
        seg = y[..., k * block:(k + 1) * block]
        r = seg.sum(-1) - mean * seg.shape[-1]
        ss = ss + r * r
    return np.sqrt(nb / (nb - 1) * ss / (n * n))


def bounds_bootstrap(x, n_boot, rng, alpha=0.10, block=BLOCK):
    """(нижняя, верхняя) перцентильного бутстрепа и бутстрепа-t для одного ряда x; индексы квантилей - как в portfolio.block_bootstrap."""
    y, _ = _resample(x, n_boot, rng, block)
    m = y.mean(1)
    ms = np.sort(m)
    i_lo, i_hi = min(n_boot - 1, int(alpha / 2 * n_boot)), min(n_boot - 1, int((1 - alpha / 2) * n_boot))
    mean = x.mean()
    se = _batch_se(y, block)
    se_hat = float(_batch_se(x, block))
    with np.errstate(all="ignore"):
        t = np.where(se > 0, (m - mean) / se, np.where(m == mean, 0.0, np.sign(m - mean) * np.inf))
    ts = np.sort(t)
    if se_hat == 0.0:
        bt = (mean, mean)
    else:
        bt = (mean - ts[i_hi] * se_hat, mean - ts[i_lo] * se_hat)
    return (ms[i_lo], ms[i_hi]), bt


def bounds_t_block(x, alpha=0.10, block=BLOCK):
    _, lo, hi, _, _ = P.block_t_interval(list(map(float, x)), alpha, block)
    return lo, hi


def bounds_t_iid(x, alpha=0.10):
    n = len(x)
    s = float(np.std(x, ddof=1))
    q = P.t_ppf(1 - alpha / 2, n - 1)
    m = float(x.mean())
    return m - q * s / math.sqrt(n), m + q * s / math.sqrt(n)


def run_cell(scen, n, reps, n_boot, seed, methods=METHODS):
    """-> {метод: (массив нижних, массив верхних)} на синтетическом нуле."""
    rng = np.random.default_rng(seed)
    xs = gen(rng, scen, reps, n)
    rb = np.random.default_rng(seed + 1)
    out = {m: ([], []) for m in methods}
    for i in range(reps):
        x = xs[i]
        if "percentile" in methods or "boot_t" in methods:
            pc, bt = bounds_bootstrap(x, n_boot, rb)
            if "percentile" in methods:
                out["percentile"][0].append(pc[0]); out["percentile"][1].append(pc[1])
            if "boot_t" in methods:
                out["boot_t"][0].append(bt[0]); out["boot_t"][1].append(bt[1])
        if "t_block" in methods:
            lo, hi = bounds_t_block(x)
            out["t_block"][0].append(lo); out["t_block"][1].append(hi)
        if "t_iid" in methods:
            lo, hi = bounds_t_iid(x)
            out["t_iid"][0].append(lo); out["t_iid"][1].append(hi)
    return {m: (np.array(v[0]), np.array(v[1])) for m, v in out.items()}


def delta80(lowers, sigma):
    """Наименьший сдвиг (в σ) с мощностью ≥ 80 % правила «нижняя граница > 0» (сдвиг-эквивариантность)."""
    ls = np.sort(lowers)
    q20 = ls[int(0.2 * len(ls))]
    return float("inf") if not math.isfinite(q20) else max(0.0, -q20 / sigma)


def size_rows(reps, n_boot, seed):
    rows = []
    for scen in SCENARIOS:
        for n in SIZES:
            res = run_cell(scen, n, reps, n_boot, seed)
            for m in METHODS:
                lo, hi = res[m]
                p_lo = float((lo > 0).mean())
                p_hi = float((hi < 0).mean())
                se = math.sqrt(0.05 * 0.95 / reps)
                rows.append([scen, n, reps, m, "%.4f" % p_lo, "%.4f" % p_hi, "%.4f" % se,
                             "%.3f" % delta80(lo, SIGMA[scen])])
            print(scen, n, " ".join("%s=%.3f" % (m, (res[m][0] > 0).mean()) for m in METHODS), flush=True)
    return rows


def joint_rows(reps, seed):
    """Условие (б′): совместный размер и мощность (а)&(б′) для нормальных суток; контроль - независимая копия (rho) того же нуля."""
    rows = []
    for n in SIZES:
        for rho in (0.0, 0.5):
            rng = np.random.default_rng(seed)
            la, ld = [], []
            for _ in range(reps):
                x = rng.normal(size=n)
                c = rho * x + math.sqrt(1 - rho * rho) * rng.normal(size=n)
                la.append(bounds_t_block(x)[0])
                ld.append(bounds_t_block(x - c)[0])
            la, ld = np.array(la), np.array(ld)
            joint = np.minimum(la, ld)
            rows.append([n, reps, rho, "%.4f" % (la > 0).mean(), "%.4f" % (ld > 0).mean(), "%.4f" % (joint > 0).mean(),
                         "%.3f" % delta80(la, 1.0), "%.3f" % delta80(joint, 1.0)])
            print("joint", n, rho, rows[-1][3:], flush=True)
    return rows


def neff_rows(reps, seed):
    eff = _load("effective_n", os.path.join(HERE, "..", "..", "..", ".claude", "skills", "alpha-research", "scripts", "effective_n.py"))
    rows = []
    for scen in ("normal", "ar1"):
        for n in SIZES:
            rng = np.random.default_rng(seed)
            xs = gen(rng, scen, reps, n)
            ne = np.array([eff.effective_n_from_autocorr(xs[i]) for i in range(reps)])
            rows.append([scen, n, reps, math.ceil(n / BLOCK), "%.2f" % ne.mean(), "%.2f" % np.median(ne), ""])
            print("neff", rows[-1], flush=True)
    rng = np.random.default_rng(seed)
    s7 = np.std(rng.normal(size=(reps, 7)), axis=1, ddof=1)
    rows.append(["sigma_form_m7", 7, reps, "", "", "", "sd(sigma_hat)/sigma = %.3f; аналитически 1/sqrt(2*(m-1)) = %.3f" % (s7.std(), 1 / math.sqrt(2 * 6))])
    return rows


def write_csv(path, header, rows):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, required=True)
    ap.add_argument("--boot", type=int, required=True)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--out-prefix", required=True)
    a = ap.parse_args()
    if a.reps < 2000:
        sys.exit("reps < 2000: размер не измерить с ошибкой < 0,5 п.п.")
    write_csv(a.out_prefix + "-size.csv",
              ["scenario", "n_days", "reps", "method", "p_lower_gt0", "p_upper_lt0", "mc_se_at_5pct", "delta80_sigma"],
              size_rows(a.reps, a.boot, a.seed))
    write_csv(a.out_prefix + "-joint.csv",
              ["n_days", "reps", "rho_control", "p_a", "p_b_diff", "p_a_and_b", "delta80_a_sigma", "delta80_a_and_b_sigma"],
              joint_rows(a.reps, a.seed))
    write_csv(a.out_prefix + "-neff.csv",
              ["scenario", "n_days", "reps", "n_eff_ceil_n3", "n_eff_autocorr_mean", "n_eff_autocorr_median", "note"],
              neff_rows(a.reps, a.seed))


if __name__ == "__main__":
    main()
