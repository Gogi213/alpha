#!/usr/bin/env python3
"""Статистические утилиты: кластерный бутстреп (seed 37, B = 3000), Вильсон с эффективным n, Каплан-Мейер, блочные суточные интервалы (portfolio.py), МРЭ. Только numpy."""
import math
import os
import sys

import numpy as np

B_BOOT = 3000
SEED = 37
Z90 = 1.6448536269514722        # односторонний 5 % = граница 90 %-интервала


def load_tables(path):
    import json
    Z = np.load(path)
    meta = json.load(open(path.replace(".npz", ".json"), encoding="utf-8"))
    tr = {c: Z["tr"][:, i] for i, c in enumerate(meta["tr_cols"])}
    ep = {c: Z["ep"][:, i] for i, c in enumerate(meta["ep_cols"])}
    return tr, ep, Z["hints"], Z["act"], meta


def cluster_ids(*keys):
    """Целочисленные номера кластеров по набору ключей (кортеж значений)."""
    k = np.column_stack([np.asarray(x, np.int64) for x in keys])
    _, inv = np.unique(k, axis=0, return_inverse=True)
    return inv.ravel()


def boot_weights(n_cl, B=B_BOOT, seed=SEED):
    """Мультиномиальные веса кластеров [B, n_cl] (ресэмплирование n_cl кластеров с возвращением), порциями."""
    rng = np.random.default_rng(seed)
    p = np.full(n_cl, 1.0 / n_cl)
    for a in range(0, B, 250):
        b = min(250, B - a)
        yield rng.multinomial(n_cl, p, size=b).astype(np.float64)


def ratio_boot(cl, num, den, B=B_BOOT, seed=SEED, q=(0.05, 0.95)):
    """Кластерный бутстреп отношения сумм sum(num)/sum(den) (num, den: [n] или [n, h]); cl - номера кластеров. -> (точечная оценка, нижняя, верхняя, se, все ресэмплы)."""
    num = np.asarray(num, float)
    den = np.asarray(den, float)
    if num.ndim == 1:
        num, den = num[:, None], den[:, None]
    n_cl = int(cl.max()) + 1
    Sn = np.zeros((n_cl, num.shape[1]))
    Sd = np.zeros((n_cl, num.shape[1]))
    for j in range(num.shape[1]):
        Sn[:, j] = np.bincount(cl, weights=num[:, j], minlength=n_cl)
        Sd[:, j] = np.bincount(cl, weights=den[:, j], minlength=n_cl)
    pt = Sn.sum(0) / np.where(Sd.sum(0) > 0, Sd.sum(0), np.nan)
    res = []
    for w in boot_weights(n_cl, B, seed):
        N, D = w @ Sn, w @ Sd
        with np.errstate(invalid="ignore", divide="ignore"):
            res.append(N / np.where(D > 0, D, np.nan))
    res = np.vstack(res)
    lo, hi = np.nanquantile(res, q[0], axis=0), np.nanquantile(res, q[1], axis=0)
    se = np.nanstd(res, axis=0, ddof=1)
    return pt, lo, hi, se, res


def wilson(p, n, z=Z90):
    """Интервал Вильсона (доля p при числе наблюдений n; n может быть дробным - эффективный). -> (низ, верх)."""
    if n <= 0 or not np.isfinite(p):
        return float("nan"), float("nan")
    d = 1.0 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def cluster_wilson(y, cl, B=B_BOOT, seed=SEED):
    """Доля y (0/1) с интервалом Вильсона при эффективном n = p(1-p)/Var_кластерного_бутстрепа (не больше числа наблюдений и не меньше числа кластеров/... нет, зажато в [1, n]).
    -> (p, n, n_eff, lo, hi, se_boot, n_кластеров)."""
    y = np.asarray(y, float)
    n = len(y)
    if n == 0:
        return (float("nan"), 0, 0.0, float("nan"), float("nan"), float("nan"), 0)
    p = float(y.mean())
    ncl = int(cl.max()) + 1
    if p in (0.0, 1.0):
        lo, hi = wilson(p, max(float(ncl), 1.0))        # без разброса доли n_eff не оценить: консервативно число кластеров
        return (p, n, float(ncl), lo, hi, 0.0, ncl)
    pt, _, _, se, _ = ratio_boot(cl, y, np.ones(n), B, seed)
    var_b = float(se[0]) ** 2
    n_eff = p * (1 - p) / var_b if var_b > 0 else float(n)
    n_eff = min(max(n_eff, 1.0), float(n))
    lo, hi = wilson(p, n_eff)
    return (p, n, n_eff, lo, hi, float(se[0]), ncl)


def km_exact(D, cens):
    """Каплан-Мейер S(m) = P(D > m) в точках дискретного времени. -> (времена, S)."""
    D = np.asarray(D, float)
    cens = np.asarray(cens, float)
    o = np.argsort(D, kind="stable")
    t, c = D[o], cens[o]
    uq, idx = np.unique(t, return_index=True)
    nr = (len(t) - idx).astype(float)
    ev = np.add.reduceat((c == 0).astype(float), idx)
    return uq, np.cumprod(1.0 - ev / nr)


def km_at(D, cens, m):
    uq, S = km_exact(D, cens)
    i = np.searchsorted(uq, m, side="right") - 1
    return 1.0 if i < 0 else float(S[i])


# ------------------------------------------------------------------ суточные интервалы (portfolio.py, тот же код, что у статуса)
def portfolio():
    import importlib.util
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, "..", "..", "..", "tools", "compute", "statarb", "portfolio.py"), os.path.expanduser("~/xdl9/c2t/code_repo/tools/compute/statarb/portfolio.py")):
        if os.path.isfile(cand):
            sp = importlib.util.spec_from_file_location("portfolio", cand)
            m = importlib.util.module_from_spec(sp)
            sp.loader.exec_module(m)
            return m
    raise FileNotFoundError("portfolio.py")


def mde_table(sd, n, power=0.80, alpha_two=0.10):
    """Минимально обнаружимый эффект суточного ряда y_d из n суток при блоке ⌈n^{1/3}⌉: n_eff = ⌈n/блок⌉, df = n_eff - 1.
    -> dict: n_eff, df, se_mean = sd/sqrt(n_eff) (блочная оценка), mde50 = t_{0,95,df}·se (медиана нижней границы = 0), mde_power = (t_{0,95,df} + z_power)·se, mre_protocol = 2,8·sd/sqrt(n_eff),
    mre_consistent = (z_0,95 + z_power)·sd/sqrt(n_eff)."""
    P = portfolio()
    b = P.block_len(n)
    ne = math.ceil(n / b)
    df = ne - 1
    nd = __import__("statistics").NormalDist()
    zp = nd.inv_cdf(power)
    t95 = P.t_ppf(0.95, df) if df >= 1 else float("inf")
    se = sd / math.sqrt(ne)
    return dict(n=n, block=b, n_eff=ne, df=df, se=se, mde50=t95 * se, mde_power=(t95 + zp) * se, mre_protocol=2.8 * se, mre_consistent=(nd.inv_cdf(0.95) + zp) * se)
