"""TK-458 B2 шаг G4.3: образование имбаланса (FVG) -> fulfill за N=10 баров, контроль C2. Протокол docs/research/P-TK458-b2-g4-2026-10-30.md.
python -I tools/bp_g4.py <каталог csv.gz> <выход-префикс> [SYM ...]"""
import sys, glob, os
import numpy as np, pandas as pd

TFS = {"H1": 60, "H4": 240, "D1": 1440}; N = 10; NC = 5; WIN = 30


def bars(path, tf):
    d = pd.read_csv(path, usecols=[0, 1, 2, 3, 4]); d.columns = ["t", "o", "h", "l", "c"]
    g = d.t // (tf * 60000)
    a = d.groupby(g, sort=True).agg(h=("h", "max"), l=("l", "min"), c=("c", "last"), n=("t", "size"))
    a = a.reindex(pd.RangeIndex(a.index.min(), a.index.max() + 1))
    ok = (a.n == tf).fillna(False).to_numpy()
    return a.index.to_numpy(), a.h.to_numpy(), a.l.to_numpy(), a.c.to_numpy(), ok


def atr14(h, l, c):
    pc = np.r_[np.nan, c[:-1]]; tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    return pd.Series(tr).rolling(14).mean().to_numpy()  # NaN в окне -> NaN


def side(idx, h, l, c, ok, tf, rng, sign):
    """sign=+1 бычий, -1 зеркало. -> строки (ts_ms, g/atr, f_ful, f_50, c_ful, c_50)"""
    if sign < 0: h, l, c = -l, -h, -c
    n = len(h); atr = atr14(h, l, c); slots = 1440 // tf
    rows = []
    gapb = np.zeros(n, bool); gapb[2:] = ok[2:] & ok[1:-1] & ok[:-2] & (l[2:] > h[:-2])
    atrr = atr / np.where(c != 0, np.abs(c), np.nan)
    for j in np.flatnonzero(gapb[2:n - N - 1]) + 2:
        if np.isnan(atr[j]) or atr[j] <= 0 or not ok[j + 1:j + 1 + N].all(): continue
        g = l[j] - h[j - 2]; d = c[j] - h[j - 2]
        ful = float(l[j + 1:j + 1 + N].min() <= h[j - 2]); f50 = float(l[j + 1:j + 1 + N].min() <= h[j - 2] + g / 2)
        lo, hi = max(j - WIN * slots, 1), min(j + WIN * slots, n - N - 2)
        cand = np.arange(lo, hi + 1); cand = cand[(cand - j) % slots == 0]
        cand = cand[(cand != j)]
        cand = cand[~gapb[cand] & ok[cand] & ~np.isnan(atrr[cand])]
        a0 = atrr[j]; cand = cand[(atrr[cand] <= a0 * 1.25) & (atrr[cand] >= a0 / 1.25)]
        cand = cand[np.array([ok[k + 1:k + 1 + N].all() for k in cand], bool)] if len(cand) else cand
        if len(cand) == 0: continue
        cs = rng.choice(cand, min(NC, len(cand)), replace=False)
        cf = np.mean([l[k + 1:k + 1 + N].min() <= c[k] - d for k in cs]); c5 = np.mean([l[k + 1:k + 1 + N].min() <= c[k] - d + g / 2 for k in cs])
        rows.append((int((idx[j] + 1) * tf * 60000), g / atr[j], ful, f50, cf, c5))
    return rows


def coin(path, rng):
    sym = os.path.basename(path).split(".")[0]; out = []
    for name, tf in TFS.items():
        idx, h, l, c, ok = bars(path, tf)
        for sign, dn in [(1, "bull"), (-1, "bear")]:
            for r in side(idx, h, l, c, ok, tf, rng, sign):
                out.append((r[0], sym, name, f"def.imbalance+fulfill:{dn}") + r[1:])
    return out


if __name__ == "__main__":
    src, out = sys.argv[1], sys.argv[2]
    fs = [f"{src}/{s}.csv.gz" for s in sys.argv[3:]] or sorted(glob.glob(src + "/*.csv.gz"))
    rng = np.random.default_rng(458)
    cols = ["ts_utc_ms", "coin", "tf", "def_id", "g_atr", "f_ful", "f_50", "c_ful", "c_50"]
    pd.DataFrame([r for f in fs for r in coin(f, rng)], columns=cols).to_csv(out + "-events.csv.gz", index=False)
