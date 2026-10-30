"""TK-458 B2 шаг G1.1: подтверждённая нога (k=2) -> откат >= X ноги раньше обновления конца (окно 50 баров), контроль C1. Протокол docs/research/P-TK458-b2-g1-2026-10-30.md.
python -I tools/bp_g1.py <каталог csv.gz> <выход-префикс> [SYM ...]"""
import sys, glob, os, importlib.util
import numpy as np, pandas as pd

_s = importlib.util.spec_from_file_location("bp_g4", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bp_g4.py"))
G = importlib.util.module_from_spec(_s); _s.loader.exec_module(G)
TFS, NC, WIN = G.TFS, G.NC, G.WIN; M = 50; XS = [0.15, 0.3, 0.5, 0.618]


def first(h, l, s, dn, up):
    """окно s..s+M-1: 1 если low<=dn раньше high>up; 0 иначе (в т.ч. один бар/ничего); -> float"""
    a = np.flatnonzero(l[s:s + M] <= dn); b = np.flatnonzero(h[s:s + M] > up)
    ia = a[0] if len(a) else M + 1; ib = b[0] if len(b) else M + 1
    return float(ia < ib)


def side(idx, h, l, c, ok, tf, rng, sign):
    if sign < 0: h, l, c = -l, -h, -c
    n = len(h); atr = G.atr14(h, l, c); slots = 1440 // tf
    atrr = atr / np.where(c != 0, np.abs(c), np.nan)
    okc = np.r_[0, np.cumsum(ok)]
    def allok(a, b): return okc[b] - okc[a] == b - a
    ph = np.zeros(n, bool); pl = np.zeros(n, bool)
    ph[2:n - 2] = (h[2:n - 2] > h[1:n - 3]) & (h[2:n - 2] > h[:n - 4]) & (h[2:n - 2] > h[3:n - 1]) & (h[2:n - 2] > h[4:])
    pl[2:n - 2] = (l[2:n - 2] < l[1:n - 3]) & (l[2:n - 2] < l[:n - 4]) & (l[2:n - 2] < l[3:n - 1]) & (l[2:n - 2] < l[4:])
    conf = np.zeros(n, bool); conf[2:] = ph[:-2]       # бар подтверждения свинга-хай
    lows = np.flatnonzero(pl); rows = []
    for b in np.flatnonzero(ph):
        t = b + 2
        if t + 1 + M >= n or b < 2 or not allok(b - 2, t + 1 + M) or np.isnan(atr[t]) or atr[t] <= 0: continue
        k = np.searchsorted(lows, b - 2, side="right") - 1      # свинг-лой, подтверждённый до t (a+2 <= t, a < b)
        if k < 0: continue
        a = lows[k]
        if not allok(a, a + 1) or a + 2 > t: continue
        Lg = h[b] - l[a]
        if Lg <= 0: continue
        lo, hi = max(t - WIN * slots, 1), min(t + WIN * slots, n - M - 2)
        cand = np.arange(lo, hi + 1); cand = cand[(cand - t) % slots == 0]; cand = cand[cand != t]
        cand = cand[~conf[cand] & ok[cand] & ~np.isnan(atrr[cand])]
        a0 = atrr[t]; cand = cand[(atrr[cand] <= a0 * 1.25) & (atrr[cand] >= a0 / 1.25)]
        cand = cand[np.array([allok(k2 + 1, k2 + 1 + M) for k2 in cand], bool)] if len(cand) else cand
        if len(cand) == 0: continue
        cs = rng.choice(cand, min(NC, len(cand)), replace=False)
        u = h[b] - c[t]; fr, cr = [], []
        for x in XS:
            R = h[b] - x * Lg; d = c[t] - R
            if l[b + 1:t + 1].min() <= R or d <= 0: fr.append(np.nan); cr.append(np.nan); continue
            fr.append(first(h, l, t + 1, R, h[b])); cr.append(np.mean([first(h, l, k2 + 1, c[k2] - d, c[k2] + u) for k2 in cs]))
        rows.append((int((idx[t] + 1) * tf * 60000), Lg / atr[t]) + tuple(fr) + tuple(cr))
    return rows


def coin(path, rng):
    sym = os.path.basename(path).split(".")[0]; out = []
    for name, tf in TFS.items():
        idx, h, l, c, ok = G.bars(path, tf)
        for sign, dn in [(1, "high"), (-1, "low")]:
            for r in side(idx, h, l, c, ok, tf, rng, sign):
                out.append((r[0], sym, name, f"def.leg_retrace:{dn}") + r[1:])
    return out


if __name__ == "__main__":
    src, out = sys.argv[1], sys.argv[2]
    fs = [f"{src}/{s}.csv.gz" for s in sys.argv[3:]] or sorted(glob.glob(src + "/*.csv.gz"))
    rng = np.random.default_rng(458)
    cols = ["ts_utc_ms", "coin", "tf", "def_id", "leg_atr"] + [f"f{i}" for i in range(4)] + [f"c{i}" for i in range(4)]
    pd.DataFrame([r for f in fs for r in coin(f, rng)], columns=cols).to_csv(out + "-events.csv.gz", index=False)
