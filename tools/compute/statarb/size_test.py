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

w5 (D12, аудиты L5-02, L1-02): (1) правило статуса (а) - нижние границы ДВУХ интервалов > 0: блочный t И студентизованный бутстреп-t (метод `both`
в -size.csv; нижняя граница `both` = min двух, верхняя = max); (2) скошенные сценарии «мелкий плюс / редкий крупный минус» (-skew.csv): в клетке
npairs = 5 активных пар (MIN_PAIRS), на каждой паре-сутки Пуассон(lam) сделок, сделка выигрывает +w с вероятностью p_win, проигрыш подобран на
нулевое среднее (p_win·w = (1−p_win)·L: отношение L/w = p/(1−p) = 4, 9, 32); значение суток = Σ сделок / npairs; печатаются выборочная асимметрия,
доля «нижняя граница > 0» у t, у бутстрепа-t и у обоих, то же с условием «сделок за окно >= 30» (г), и совместно с (б) при контроле - независимой
копии сценария (среднее 0); (3) `e2e` - те же доли на боевых функциях (portfolio.block_t_interval, portfolio.block_boot_t_interval и cell_status.classify
целиком, не векторные копии); (4) -joint.csv дополнен (а)&(б) по обеим границам.

Запуск: python size_test.py --reps 4000 --boot 3000 --seed 37 --e2e-reps 1000 --parts size,joint,skew,e2e,neff --out-prefix docs/findings/xdl9-stats-w5-2026-11-06
Только numpy и stdlib. Число повторов (--reps), B (--boot), зерно (--seed), --e2e-reps и --parts - обязательные аргументы без значений по умолчанию
(--parts: подмножество size, joint, skew, e2e, neff через запятую; части независимы - на Deck идут параллельно отдельными процессами).
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
CS = _load("statarb_cell_status_size_test", os.path.join(HERE, "cell_status.py"))

BLOCK = 3
SIGMA = {"normal": 1.0, "t3": 1.0, "sparse": math.sqrt(0.35), "ar1": 1.0 / math.sqrt(1 - 0.3 * 0.3)}
SCENARIOS = ("normal", "t3", "sparse", "ar1")
SIZES = (15, 21)
METHODS = ("percentile", "boot_t", "t_block", "t_iid", "both")
SKEW_W = 15.0                # выигрыш сделки, bps (масштаб на размер не влияет)
SKEW_NPAIRS = 5              # пар в клетке (MIN_PAIRS протокола)
SKEW_LAM = (0.5, 2.0, 8.0)   # сделок на пару-сутки (Пуассон)
SKEW_P_WIN = (0.8, 0.9, 0.97)  # отношение проигрыш/выигрыш p/(1−p): 4, 9, 32
MIN_TRADES = 30


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


def gen_skew(rng, reps, n, lam, p_win, npairs=SKEW_NPAIRS, w=SKEW_W):
    """Скошенный нуль «мелкий плюс / редкий крупный минус» (w5, L5-02): -> (значения суток (reps, n), число сделок за окно (reps,)).
    Сделок за сутки клетки Пуассон(lam·npairs); сделка +w с вероятностью p_win, иначе −L, L = p_win·w/(1−p_win) (среднее сделки 0); значение суток = Σ / npairs."""
    k = rng.poisson(lam * npairs, size=(reps, n))
    wins = rng.binomial(k, p_win)
    loss = p_win * w / (1.0 - p_win)
    return (wins * w - (k - wins) * loss) / npairs, k.sum(axis=1)


def sample_skew(x):
    """Выборочная асимметрия по строкам (g1, моменты с делением на n); NaN при нулевой дисперсии."""
    d = x - x.mean(axis=1, keepdims=True)
    m2 = (d ** 2).mean(axis=1)
    m3 = (d ** 3).mean(axis=1)
    with np.errstate(all="ignore"):
        return m3 / m2 ** 1.5


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


def boot_t_from_resamples(x, y, alpha=0.10, block=BLOCK):
    """Границы студентизованного бутстрепа-t для ряда x по готовым ресэмплам y (n_boot, n); тот же алгоритм, что `portfolio.block_boot_t_interval`."""
    n_boot = y.shape[0]
    i_lo, i_hi = min(n_boot - 1, int(alpha / 2 * n_boot)), min(n_boot - 1, int((1 - alpha / 2) * n_boot))
    mean = x.mean()
    m = y.mean(1)
    se = _batch_se(y, block)
    se_hat = float(_batch_se(x, block))
    with np.errstate(all="ignore"):
        t = np.where(se > 0, (m - mean) / se, np.where(m == mean, 0.0, np.sign(m - mean) * np.inf))
    ts = np.sort(t)
    if se_hat == 0.0:
        return mean, mean
    return mean - ts[i_hi] * se_hat, mean - ts[i_lo] * se_hat


def bounds_bootstrap(x, n_boot, rng, alpha=0.10, block=BLOCK):
    """(нижняя, верхняя) перцентильного бутстрепа и бутстрепа-t для одного ряда x; индексы квантилей - как в portfolio.block_bootstrap."""
    y, _ = _resample(x, n_boot, rng, block)
    m = y.mean(1)
    ms = np.sort(m)
    i_lo, i_hi = min(n_boot - 1, int(alpha / 2 * n_boot)), min(n_boot - 1, int((1 - alpha / 2) * n_boot))
    return (ms[i_lo], ms[i_hi]), boot_t_from_resamples(x, y, alpha, block)


def bounds_t_block(x, alpha=0.10, block=BLOCK):
    _, lo, hi, _, _ = P.block_t_interval(list(map(float, x)), alpha, block)
    return lo, hi


def bounds_t_iid(x, alpha=0.10):
    n = len(x)
    s = float(np.std(x, ddof=1))
    q = P.t_ppf(1 - alpha / 2, n - 1)
    m = float(x.mean())
    return m - q * s / math.sqrt(n), m + q * s / math.sqrt(n)


def run_series(xs, n_boot, seed, methods=METHODS):
    """-> {метод: (массив нижних, массив верхних)} по готовым рядам xs (reps, n). `both` - правило статуса (а) w5: нижняя = min, верхняя = max
    границ блочного t и бутстрепа-t (нижняя > 0 тогда и только тогда, когда обе нижние > 0)."""
    reps = xs.shape[0]
    rb = np.random.default_rng(seed + 1)
    out = {m: ([], []) for m in methods}
    for i in range(reps):
        x = xs[i]
        pc = bt = tb = None
        if any(m in methods for m in ("percentile", "boot_t", "both")):
            pc, bt = bounds_bootstrap(x, n_boot, rb)
            if "percentile" in methods:
                out["percentile"][0].append(pc[0]); out["percentile"][1].append(pc[1])
            if "boot_t" in methods:
                out["boot_t"][0].append(bt[0]); out["boot_t"][1].append(bt[1])
        if "t_block" in methods or "both" in methods:
            tb = bounds_t_block(x)
            if "t_block" in methods:
                out["t_block"][0].append(tb[0]); out["t_block"][1].append(tb[1])
        if "both" in methods:
            out["both"][0].append(min(tb[0], bt[0])); out["both"][1].append(max(tb[1], bt[1]))
        if "t_iid" in methods:
            lo, hi = bounds_t_iid(x)
            out["t_iid"][0].append(lo); out["t_iid"][1].append(hi)
    return {m: (np.array(v[0]), np.array(v[1])) for m, v in out.items()}


def run_cell(scen, n, reps, n_boot, seed, methods=METHODS):
    """-> {метод: (массив нижних, массив верхних)} на синтетическом нуле."""
    rng = np.random.default_rng(seed)
    return run_series(gen(rng, scen, reps, n), n_boot, seed, methods)


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


def joint_rows(reps, n_boot, seed):
    """Условие (б′): совместный размер и мощность (а)&(б′) для нормальных суток; контроль - независимая копия (rho) того же нуля.
    p_a / p_b_diff / p_a_and_b / delta80_* - по блочному t (как w3); *_both - нижние границы ОБОИХ интервалов (t и бутстреп-t) для (а) и (б)."""
    rows = []
    for n in SIZES:
        for rho in (0.0, 0.5):
            rng = np.random.default_rng(seed)
            rb = np.random.default_rng(seed + 1)
            la, ld, lab, ldb = [], [], [], []
            for _ in range(reps):
                x = rng.normal(size=n)
                c = rho * x + math.sqrt(1 - rho * rho) * rng.normal(size=n)
                ta, td = bounds_t_block(x)[0], bounds_t_block(x - c)[0]
                ba, bd = bounds_bootstrap(x, n_boot, rb)[1][0], bounds_bootstrap(x - c, n_boot, rb)[1][0]
                la.append(ta); ld.append(td); lab.append(min(ta, ba)); ldb.append(min(td, bd))
            la, ld, lab, ldb = np.array(la), np.array(ld), np.array(lab), np.array(ldb)
            joint = np.minimum(la, ld)
            joint_b = np.minimum(lab, ldb)
            rows.append([n, reps, rho, "%.4f" % (la > 0).mean(), "%.4f" % (ld > 0).mean(), "%.4f" % (joint > 0).mean(),
                         "%.3f" % delta80(la, 1.0), "%.3f" % delta80(joint, 1.0),
                         "%.4f" % (lab > 0).mean(), "%.4f" % (ldb > 0).mean(), "%.4f" % (joint_b > 0).mean(), "%.3f" % delta80(lab, 1.0), "%.3f" % delta80(joint_b, 1.0)])
            print("joint", n, rho, rows[-1][3:], flush=True)
    return rows


def skew_rows(reps, n_boot, seed):
    """Размер правил на скошенном нуле (w5, D12). Для каждого (n, lam, p_win): асимметрия, доля «нижняя > 0» у t / бутстрепа-t / обоих,
    то же при >= 30 сделок за окно (г), и совместно с (б) при независимом контроле-копии (среднее 0)."""
    rows = []
    for n in SIZES:
        for lam in SKEW_LAM:
            for p_win in SKEW_P_WIN:
                tag = int(round(lam * 100 + p_win * 1000))
                rng = np.random.default_rng(seed + tag)
                xs, ntr = gen_skew(rng, reps, n, lam, p_win)
                cs_, _ = gen_skew(rng, reps, n, lam, p_win)            # контроль: независимая копия того же нуля
                res = run_series(xs, n_boot, seed + tag, methods=("t_block", "boot_t", "both"))
                dres = run_series(xs - cs_, n_boot, seed + tag + 7, methods=("both",))
                a_t, a_b, a_both = (res[m][0] > 0 for m in ("t_block", "boot_t", "both"))
                b_both = dres["both"][0] > 0
                ok = ntr >= MIN_TRADES
                sk = sample_skew(xs)
                rows.append([n, reps, lam, p_win, "%.0f" % (p_win / (1 - p_win)), SKEW_NPAIRS, "%.2f" % float(np.nanmean(sk)), "%.1f" % float(ntr.mean()),
                             "%.4f" % a_t.mean(), "%.4f" % a_b.mean(), "%.4f" % a_both.mean(),
                             "%.4f" % ok.mean(), "%.4f" % (a_t & ok).mean(), "%.4f" % (a_both & ok).mean(),
                             "%.4f" % b_both.mean(), "%.4f" % (a_both & b_both).mean(), "%.4f" % (a_both & b_both & ok).mean()])
                print("skew", n, lam, p_win, rows[-1][6:], flush=True)
    return rows


def e2e_rows(reps, seed):
    """Размер на БОЕВЫХ функциях: portfolio.block_t_interval, portfolio.block_boot_t_interval (чистый Python) и cell_status.classify целиком
    (остальные условия (в)-(ж) выполнены по построению; контроль - независимая копия сценария со средним 0). Сценарии: нормальные, разреженные,
    скошенные lam = 2 при p_win 0,8 / 0,9 и lam = 0,5 при p_win 0,9."""
    base = dict(n_trades=60, n_pairs=8, sigma_form=3.0, mean_without_best_coin=1.0, net_plus1=1.0)
    scen = [("normal", None, None), ("sparse", None, None), ("skew", 2.0, 0.8), ("skew", 2.0, 0.9), ("skew", 0.5, 0.9)]
    rows = []
    for n in SIZES:
        for name, lam, p_win in scen:
            rng = np.random.default_rng(seed + n)
            if name == "skew":
                xs, _ = gen_skew(rng, reps, n, lam, p_win)
                cs_, _ = gen_skew(rng, reps, n, lam, p_win)
            else:
                xs, cs_ = gen(rng, name, reps, n), gen(rng, name, reps, n)
            n_t = n_bt = n_both = n_cand = 0
            for i in range(reps):
                r = CS.classify(list(map(float, xs[i])), list(map(float, cs_[i])), **base)
                n_t += r["cond_a_t"]; n_bt += r["cond_a_boot"]; n_both += r["cond_a"]; n_cand += r["status"] == CS.CANDIDATE
            rows.append([n, reps, name, "" if lam is None else lam, "" if p_win is None else p_win,
                         "%.4f" % (n_t / reps), "%.4f" % (n_bt / reps), "%.4f" % (n_both / reps), "%.4f" % (n_cand / reps)])
            print("e2e", rows[-1], flush=True)
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


PARTS = ("size", "joint", "skew", "e2e", "neff")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, required=True)
    ap.add_argument("--boot", type=int, required=True)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--e2e-reps", type=int, required=True, dest="e2e_reps")
    ap.add_argument("--parts", required=True, help="через запятую: " + ",".join(PARTS))
    ap.add_argument("--out-prefix", required=True)
    a = ap.parse_args()
    parts = [x.strip() for x in a.parts.split(",") if x.strip()]
    bad = [x for x in parts if x not in PARTS]
    if bad:
        sys.exit("неизвестные части: %s" % bad)
    if a.reps < 2000:
        sys.exit("reps < 2000: размер не измерить с ошибкой < 0,5 п.п.")
    if "size" in parts:
        write_csv(a.out_prefix + "-size.csv",
                  ["scenario", "n_days", "reps", "method", "p_lower_gt0", "p_upper_lt0", "mc_se_at_5pct", "delta80_sigma"],
                  size_rows(a.reps, a.boot, a.seed))
    if "joint" in parts:
        write_csv(a.out_prefix + "-joint.csv",
                  ["n_days", "reps", "rho_control", "p_a", "p_b_diff", "p_a_and_b", "delta80_a_sigma", "delta80_a_and_b_sigma",
                   "p_a_both", "p_b_diff_both", "p_a_and_b_both", "delta80_a_both_sigma", "delta80_a_and_b_both_sigma"],
                  joint_rows(a.reps, a.boot, a.seed))
    if "skew" in parts:
        write_csv(a.out_prefix + "-skew.csv",
                  ["n_days", "reps", "lam_per_pair_day", "p_win", "loss_over_win", "npairs", "sample_skew_mean", "trades_per_window_mean",
                   "p_a_t", "p_a_boot_t", "p_a_both", "p_trades_ge30", "p_a_t_and_ge30", "p_a_both_and_ge30",
                   "p_b_both_control_copy", "p_a_and_b_both", "p_a_and_b_both_and_ge30"],
                  skew_rows(a.reps, a.boot, a.seed))
    if "e2e" in parts:
        if a.e2e_reps < 200:
            sys.exit("e2e-reps < 200")
        write_csv(a.out_prefix + "-e2e.csv",
                  ["n_days", "reps", "scenario", "lam_per_pair_day", "p_win", "p_cond_a_t", "p_cond_a_boot_t", "p_cond_a_both", "p_status_candidate"],
                  e2e_rows(a.e2e_reps, a.seed))
    if "neff" in parts:
        write_csv(a.out_prefix + "-neff.csv",
                  ["scenario", "n_days", "reps", "n_eff_ceil_n3", "n_eff_autocorr_mean", "n_eff_autocorr_median", "note"],
                  neff_rows(a.reps, a.seed))


if __name__ == "__main__":
    main()
