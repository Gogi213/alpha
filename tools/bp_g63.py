"""TK-458 B2 шаг G6.3: сильный M5-имбаланс в окне Лондона: частота против остальных часов NY-даты, исход fulfill против C2. Протокол docs/research/P-TK458-b2-g63-2026-10-30.md.
python -I tools/bp_g63.py <каталог csv.gz> <выход-префикс> [SYM ...]"""
import sys, glob, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
from bp_g4 import bars, atr14, N, NC, WIN

TF = 5; MIN_L, MIN_O = 50, 150


def ny_hours(idx):
    t = pd.to_datetime(idx.astype("int64") * TF * 60000, unit="ms", utc=True).tz_convert("America/New_York")
    return np.asarray(t.hour), np.asarray(t.year * 10000 + t.month * 100 + t.day)


def lon_masks(hr, comp):
    """тройки по j (бары j-2..j): в Лондоне — бар j-2 с 03:00, бар j до 08:00; вне — все три бара вне 03–08."""
    n = len(hr); inl = (hr >= 3) & (hr < 8)
    L = np.zeros(n, bool); L[2:] = (hr[:-2] >= 3) & (hr[2:] < 8) & (hr[2:] >= 3)
    O = np.zeros(n, bool); O[2:] = ~inl[2:] & ~inl[1:-1] & ~inl[:-2]
    return L & comp, O & comp


def events(idx, h, l, c, ok, hr, rng, sign):
    """сильные имбалансы с t0 в Лондоне и исход fulfill/контроль C2 (как bp_g4.side)."""
    if sign < 0: h, l, c = -l, -h, -c
    n = len(h); atr = atr14(h, l, c); slots = 1440 // TF
    atrr = atr / np.where(c != 0, np.abs(c), np.nan)
    gapb = np.zeros(n, bool); gapb[2:] = ok[2:] & ok[1:-1] & ok[:-2] & (l[2:] > h[:-2])
    comp = np.zeros(n, bool); comp[2:] = ok[2:] & ok[1:-1] & ok[:-2] & ~np.isnan(atr[2:]) & (atr[2:] > 0)
    gsz = np.zeros(n); gsz[2:] = l[2:] - h[:-2]
    L, _ = lon_masks(hr, comp)
    rows = []
    for j in np.flatnonzero(gapb & L & (gsz >= atr / 4))[:]:
        if j >= n - N - 2 or not ok[j + 1:j + 1 + N].all(): continue
        g = gsz[j]; d = c[j] - h[j - 2]
        ful = float(l[j + 1:j + 1 + N].min() <= h[j - 2])
        lo, hi = max(j - WIN * slots, 1), min(j + WIN * slots, n - N - 2)
        cand = np.arange(lo, hi + 1); cand = cand[(cand - j) % slots == 0]; cand = cand[cand != j]
        cand = cand[~gapb[cand] & ok[cand] & ~np.isnan(atrr[cand])]
        a0 = atrr[j]; cand = cand[(atrr[cand] <= a0 * 1.25) & (atrr[cand] >= a0 / 1.25)]
        cand = cand[np.array([ok[k + 1:k + 1 + N].all() for k in cand], bool)] if len(cand) else cand
        if len(cand) == 0: continue
        cs = rng.choice(cand, min(NC, len(cand)), replace=False)
        cf = np.mean([l[k + 1:k + 1 + N].min() <= c[k] - d for k in cs])
        rows.append((int((idx[j] + 1) * TF * 60000), g / atr[j], ful, cf))
    return rows


def coin(path, rng):
    sym = os.path.basename(path).split(".")[0]
    idx, h, l, c, ok = bars(path, TF); hr, day = ny_hours(idx); n = len(h)
    a = atr14(h, l, c); comp = np.zeros(n, bool); comp[2:] = ok[2:] & ok[1:-1] & ok[:-2] & ~np.isnan(a[2:]) & (a[2:] > 0)
    up = np.zeros(n); up[2:] = l[2:] - h[:-2]; dn = np.zeros(n); dn[2:] = l[:-2] - h[2:]
    sg = comp & ((up >= a / 4) | (dn >= a / 4))
    L, O = lon_masks(hr, comp)
    df = pd.DataFrame({"day": day, "L": L, "O": O, "sL": sg & L, "sO": sg & O, "t": idx})
    gr = df.groupby("day").agg(nL=("L", "sum"), nO=("O", "sum"), kL=("sL", "sum"), kO=("sO", "sum"), t=("t", "min"))
    gr = gr[(gr.nL >= MIN_L) & (gr.nO >= MIN_O)]
    freq = [(int(r.t * TF * 60000), sym, "M5", "def.imbalance+london:freq", r.kL / r.nL - r.kO / r.nO) for r in gr.itertuples()]
    out = []
    for sign, dn_ in [(1, "bull"), (-1, "bear")]:
        for r in events(idx, h, l, c, ok, hr, rng, sign):
            out.append((r[0], sym, "M5", f"def.imbalance+london:{dn_}") + r[1:])
    return freq, out


if __name__ == "__main__":
    src, o = sys.argv[1], sys.argv[2]
    fs = [f"{src}/{s}.csv.gz" for s in sys.argv[3:]] or sorted(glob.glob(src + "/*.csv.gz"))
    rng = np.random.default_rng(458); F, O = [], []
    for f in fs:
        a_, b_ = coin(f, rng); F += a_; O += b_
    pd.DataFrame(F, columns=["ts_utc_ms", "coin", "tf", "def_id", "delta"]).to_csv(o + "-freq-events.csv.gz", index=False)
    pd.DataFrame(O, columns=["ts_utc_ms", "coin", "tf", "def_id", "g_atr", "f_ful", "c_ful"]).to_csv(o + "-out-events.csv.gz", index=False)
