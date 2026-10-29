#!/usr/bin/env python3
"""TK-451 слой 1: агрегатор осей по таблицам l1-*.csv.gz (sancho.py l1). Кривые по осям, интервал — бутстреп по дням-кластерам.

python sancho_l1agg.py <каталог l1> <выход.csv>
Ячейка = (заявление, ТФ, k, направление, N, исход x1/x2, окна K/g, масштаб глубины). Допуск автора и попадание — в колонке hit.
CI по дням (B=400, seed 451) — для 1H и подмножества BTC+ETH; для остальных ТФ — только точка и «независимый» интервал.
"""
import glob
import sys
import numpy as np
import pandas as pd

NS = (1, 2, 4, 8, 10, 16, 32, 50)
KI = (3, 4, 8, 16)
KB = (1, 2, 4, 8, 16)
GS = (0.0, 0.125, 0.25, 0.5)
CLAIM = {  # (значение %, допуск ±) — полушаг последнего знака
    "A1": (61.0, 0.5), "A2": (49.2, 0.05), "A3": (53.9, 0.05), "A4": (66.5, 0.05), "A5": (62.2, 0.05),
    "A6": (84.1, 0.05), "A7": (89.0, 0.5), "A8": (91.6, 0.05)}
SHARE = {"A6": 28.2, "A7": 28.8, "A8": 15.0}
B = 400


class Cl:
    """кластеры-дни: суммы по дню числителя/знаменателя -> бутстреп"""
    def __init__(self, day):
        u, inv = np.unique(day, return_inverse=True)
        self.inv, self.D = inv, len(u)
        rng = np.random.default_rng(451)
        self.idx = rng.integers(0, self.D, (B, self.D))

    def ci(self, num_mask, den_mask):
        nu = np.bincount(self.inv, weights=num_mask, minlength=self.D)
        de = np.bincount(self.inv, weights=den_mask, minlength=self.D)
        n, d = nu[self.idx].sum(1), de[self.idx].sum(1)
        ok = d > 0
        if ok.sum() < B // 2:
            return np.nan, np.nan, self.D
        p = 100 * n[ok] / d[ok]
        return np.percentile(p, 2.5), np.percentile(p, 97.5), self.D


def wil(p, n):
    if n == 0:
        return np.nan, np.nan
    s = 1.96 * np.sqrt(p * (1 - p) / n)
    return 100 * (p - s), 100 * (p + s)


def cells(d, tf, k, dirn, with_ci):
    """d — таблица событий (после фильтра направления). Генерирует строки ячеек."""
    cl = Cl((d.t.values // 86400000)) if with_ci and len(d) else None
    brk = d.brk.values == 1
    depth = d.depth.values
    sw = ~brk

    def emit(claim, N, oc, mask, base, **ax):
        # mask — условие внутри base; исход oc в {0,1,-1}
        col = d[f"{oc}_{N}"].values
        ok = base & (col >= 0)
        num = (ok & mask & (col == 1)).astype(float)
        den = (ok & mask).astype(float)
        n = int(den.sum())
        if n == 0:
            return None
        p = num.sum() / n
        lo_i, hi_i = wil(p, n)
        lo, hi, D = cl.ci(num, den) if cl is not None else (np.nan, np.nan, 0)
        share = 100 * den.sum() / max(1, (base & (col >= 0)).sum())
        v, tol = CLAIM[claim]
        a, b = (lo, hi) if not np.isnan(lo) else (100 * p, 100 * p)
        hit = int(a <= v + tol and b >= v - tol)
        return dict(claim=claim, tf=tf, k=k, dir=dirn, N=N, oc=oc, n=n, p=100 * p, lo=lo, hi=hi, lo_ind=lo_i, hi_ind=hi_i,
                    share=share, days=D, hit=hit, **ax)

    out = []
    allm = np.ones(len(d), bool)
    for N in NS:
        for oc in ("x1", "x2"):
            r = emit("A1", N, oc, allm, brk, K_I=0, g=0, K_B=0, sc=0)
            if r: out.append(r)
            r = emit("A5", N, oc, allm, sw, K_I=0, g=0, K_B=0, sc=0)
            if r: out.append(r)
            for sc in (1.0, 1.25):
                for nm, lo_, hi_ in (("A2", 0, .2), ("A3", .2, .5), ("A4", .5, 9e9)):
                    m = (depth >= lo_ * sc) & (depth < hi_ * sc) if nm != "A4" else (depth >= .5 * sc)
                    r = emit(nm, N, oc, m, brk, K_I=0, g=0, K_B=0, sc=sc)
                    if r: out.append(r)
    # условия по свипам (окна)
    for N in NS:
        for oc in ("x1", "x2"):
            for Ki in KI:
                gap = d[f"gap_{Ki}"].values
                vi = sw & ~np.isnan(gap)
                for g in GS:
                    r = emit("A6", N, oc, gap > g, vi, K_I=Ki, g=g, K_B=0, sc=0)
                    if r: out.append(r)
            for Kb in KB:
                bos = d[f"bos_{Kb}"].values
                vb = sw & (bos >= 0)
                r = emit("A7", N, oc, bos == 1, vb, K_I=0, g=0, K_B=Kb, sc=0)
                if r: out.append(r)
            if N == 10:  # A8 — полная сетка только на якоре N
                for Ki in KI:
                    gap = d[f"gap_{Ki}"].values
                    for g in GS:
                        for Kb in KB:
                            bos = d[f"bos_{Kb}"].values
                            vv = sw & ~np.isnan(gap) & (bos >= 0)
                            r = emit("A8", N, oc, (gap > g) & (bos == 1), vv, K_I=Ki, g=g, K_B=Kb, sc=0)
                            if r: out.append(r)
    # A9 — медиана прокола по свипам
    w = d.wick.values[sw & np.isfinite(d.wick.values)]
    if len(w):
        out.append(dict(claim="A9", tf=tf, k=k, dir=dirn, N=0, oc="med", n=len(w), p=float(np.median(w)), lo=np.nan, hi=np.nan,
                        lo_ind=np.nan, hi_ind=np.nan, share=100.0, days=0, hit=int(abs(np.median(w) - .27) <= .005), K_I=0, g=0, K_B=0, sc=0))
    # воронка: доли
    out.append(dict(claim="TOUCHES", tf=tf, k=k, dir=dirn, N=0, oc="n", n=len(d), p=100 * brk.mean() if len(d) else np.nan,
                    lo=np.nan, hi=np.nan, lo_ind=np.nan, hi_ind=np.nan, share=100 * sw.mean() if len(d) else np.nan, days=0,
                    hit=0, K_I=0, g=0, K_B=0, sc=0))
    return out


def load(dirp, tf, k, syms=None):
    fs = sorted(glob.glob(f"{dirp}/l1-*-{tf}-{k}.csv.gz"))
    if syms:
        fs = [f for f in fs if f.split("/")[-1].split("-")[1] in syms]
    f64 = ("depth", "wick", "atr_bps", "gap_3", "gap_4", "gap_8", "gap_16")  # вещественные — как есть; x*/brk — int8, age — int32, bos — float32 (экономия памяти 5m)
    cols = pd.read_csv(fs[0], nrows=0).columns
    dt = {c: ("int64" if c == "t" else "int32" if c == "age" else "category" if c in ("sym", "dir") else "float64" if c in f64 else "float32" if c.startswith("bos_") else "int8") for c in cols}
    ds = [pd.read_csv(f, dtype=dt) for f in fs]
    d = pd.concat(ds, ignore_index=True)
    d["dir"] = d["dir"].astype(str)
    del ds
    # деление на ноль в ATR: бесконечные/NaN — вон
    bad = ~np.isfinite(d.wick) | ~np.isfinite(d.atr_bps) | (d.brk.eq(1) & ~np.isfinite(d.depth))
    return d[~bad].reset_index(drop=True), len(fs), int(bad.sum())


def main():
    dirp, outp = sys.argv[1], sys.argv[2]
    plan = [(60, 2), (60, 1), (60, 4), (60, 8), (5, 2), (5, 1), (15, 2), (15, 1), (30, 2), (30, 1),
            (240, 2), (240, 1), (1440, 2), (1440, 1), (10080, 2), (10080, 1)]
    rows, funnel = [], []
    for tf, k in plan:
        d, nf, nbad = load(dirp, tf, k)
        funnel.append((tf, k, nf, len(d), nbad))
        print("load", tf, k, nf, len(d), nbad, flush=True)
        for dirn, dd in (("all", d), ("H", d[d.dir == "H"]), ("L", d[d.dir == "L"])):
            if dirn != "all" and tf != 60:
                continue
            rows += [dict(subset="all84", **r) for r in cells(dd.reset_index(drop=True), tf, k, dirn, with_ci=(tf == 60))]
        if tf == 60 and k in (1, 2):
            bt, _, _ = load(dirp, tf, k, syms={"BTCUSDT", "ETHUSDT"})
            rows += [dict(subset="BTC+ETH", **r) for r in cells(bt, tf, k, "all", with_ci=True)]
            for s in ("BTCUSDT", "ETHUSDT"):
                b1, _, _ = load(dirp, tf, k, syms={s})
                rows += [dict(subset=s, **r) for r in cells(b1, tf, k, "all", with_ci=True)]
        pd.DataFrame(rows).to_csv(outp, index=False)
    pd.DataFrame(funnel, columns=["tf", "k", "files", "rows", "bad"]).to_csv(outp.replace(".csv", "-funnel.csv"), index=False)
    print("DONE", len(rows))


if __name__ == "__main__":
    main()
