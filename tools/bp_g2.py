"""TK-458 B2 шаг G2: нога (k=2) -> касание уровня отката X (0,3 / 0,5 / 0,618) раньше обновления конца -> удержание через 10 баров, контроль C1. Протокол docs/research/P-TK458-b2-g2-2026-10-30.md.
python -I tools/bp_g2.py <каталог csv.gz> <выход-префикс> [SYM ...]"""
import sys, glob, os, importlib.util
import numpy as np, pandas as pd

_s = importlib.util.spec_from_file_location("bp_g4", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bp_g4.py"))
G = importlib.util.module_from_spec(_s); _s.loader.exec_module(G)
TFS, NC, WIN = G.TFS, G.NC, G.WIN; M = 50; N = 10
TESTS = [(0.3, "low", 0.5), (0.5, "close", None), (0.618, "close", None)]       # G2.1, G2.3, G2.2: (X касания, вид удержания, X границы)


def react(h, l, c, s0, touch, up, kind, bound):
    """касание: первый бар s0..s0+M-1 с low<=touch раньше (строго) high>up; удержание s..s+N: low>bound ('low') либо close[s+N]>touch ('close'); нет касания -> nan"""
    a = np.flatnonzero(l[s0:s0 + M] <= touch)
    if len(a) == 0: return np.nan
    b = np.flatnonzero(h[s0:s0 + M] > up)
    if len(b) and b[0] <= a[0]: return np.nan
    s = s0 + a[0]
    return float(l[s:s + N + 1].min() > bound) if kind == "low" else float(c[s + N] > touch)


def side(idx, h, l, c, ok, tf, rng, sign):
    if sign < 0: h, l, c = -l, -h, -c
    n = len(h); atr = G.atr14(h, l, c); slots = 1440 // tf
    atrr = atr / np.where(c != 0, np.abs(c), np.nan)
    okc = np.r_[0, np.cumsum(ok)]
    def allok(a, b): return okc[b] - okc[a] == b - a
    ph = np.zeros(n, bool); pl = np.zeros(n, bool)
    ph[2:n - 2] = (h[2:n - 2] > h[1:n - 3]) & (h[2:n - 2] > h[:n - 4]) & (h[2:n - 2] > h[3:n - 1]) & (h[2:n - 2] > h[4:])
    pl[2:n - 2] = (l[2:n - 2] < l[1:n - 3]) & (l[2:n - 2] < l[:n - 4]) & (l[2:n - 2] < l[3:n - 1]) & (l[2:n - 2] < l[4:])
    conf = np.zeros(n, bool); conf[2:] = ph[:-2]
    lows = np.flatnonzero(pl); rows = []; T = M + N + 1
    for b in np.flatnonzero(ph):
        t = b + 2
        if t + 1 + T >= n or b < 2 or not allok(b - 2, t + 1 + T) or np.isnan(atr[t]) or atr[t] <= 0: continue
        k = np.searchsorted(lows, b - 2, side="right") - 1
        if k < 0: continue
        a = lows[k]
        if not allok(a, a + 1) or a + 2 > t: continue
        Lg = h[b] - l[a]
        if Lg <= 0: continue
        lo, hi = max(t - WIN * slots, 1), min(t + WIN * slots, n - T - 2)
        cand = np.arange(lo, hi + 1); cand = cand[(cand - t) % slots == 0]; cand = cand[cand != t]
        cand = cand[~conf[cand] & ok[cand] & ~np.isnan(atrr[cand])]
        a0 = atrr[t]; cand = cand[(atrr[cand] <= a0 * 1.25) & (atrr[cand] >= a0 / 1.25)]
        cand = cand[np.array([allok(k2 + 1, k2 + 1 + T) for k2 in cand], bool)] if len(cand) else cand
        if len(cand) == 0: continue
        cs = rng.choice(cand, min(NC, len(cand)), replace=False)
        u = h[b] - c[t]; fr, cr = [], []
        for x, kind, xb in TESTS:
            R = h[b] - x * Lg; d = c[t] - R; Rb = h[b] - xb * Lg if xb else 0.0
            if l[b + 1:t + 1].min() <= R or d <= 0: fr.append(np.nan); cr.append(np.nan); continue
            db = c[t] - Rb
            f = react(h, l, c, t + 1, R, h[b], kind, Rb)
            if np.isnan(f): fr.append(np.nan); cr.append(np.nan); continue
            cv = [react(h, l, c, k2 + 1, c[k2] - d, c[k2] + u, kind, c[k2] - db) for k2 in cs]
            cv = [v for v in cv if not np.isnan(v)]
            fr.append(f); cr.append(np.mean(cv) if cv else np.nan)
        rows.append((int((idx[t] + 1) * tf * 60000), Lg / atr[t]) + tuple(fr) + tuple(cr))
    return rows


def coin(path, rng):
    sym = os.path.basename(path).split(".")[0]; out = []
    for name, tf in TFS.items():
        idx, h, l, c, ok = G.bars(path, tf)
        for sign, dn in [(1, "high"), (-1, "low")]:
            for r in side(idx, h, l, c, ok, tf, rng, sign):
                out.append((r[0], sym, name, f"def.pd_react:{dn}") + r[1:])
    return out


if __name__ == "__main__":
    src, out = sys.argv[1], sys.argv[2]
    fs = [f"{src}/{s}.csv.gz" for s in sys.argv[3:]] or sorted(glob.glob(src + "/*.csv.gz"))
    rng = np.random.default_rng(458)
    cols = ["ts_utc_ms", "coin", "tf", "def_id", "leg_atr"] + [f"f{i}" for i in range(3)] + [f"c{i}" for i in range(3)]
    pd.DataFrame([r for f in fs for r in coin(f, rng)], columns=cols).to_csv(out + "-events.csv.gz", index=False)
