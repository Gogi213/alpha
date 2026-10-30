"""TK-458 B2 шаг G3.1: фрактал k=1 -> снятие (high > уровня) в окне 50 баров -> разворот close[s+10] < уровня, контроль C1. Протокол docs/research/P-TK458-b2-g3-2026-10-30.md.
python -I tools/bp_g3.py <каталог csv.gz> <выход-префикс> [SYM ...]"""
import sys, glob, os, importlib.util
import numpy as np, pandas as pd

_s = importlib.util.spec_from_file_location("bp_g4", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bp_g4.py"))
G = importlib.util.module_from_spec(_s); _s.loader.exec_module(G)
TFS, NC, WIN = G.TFS, G.NC, G.WIN; M = 50; N = 10


def sweep(h, c, s0, lvl):
    """первый бар s0..s0+M-1 с high>lvl -> close[s+N] < lvl (1.0/0.0); нет снятия -> nan"""
    a = np.flatnonzero(h[s0:s0 + M] > lvl)
    if len(a) == 0: return np.nan
    return float(c[s0 + a[0] + N] < lvl)


def side(idx, h, l, c, ok, tf, rng, sign):
    if sign < 0: h, l, c = -l, -h, -c
    n = len(h); atr = G.atr14(h, l, c); slots = 1440 // tf
    atrr = atr / np.where(c != 0, np.abs(c), np.nan)
    okc = np.r_[0, np.cumsum(ok)]
    def allok(a, b): return okc[b] - okc[a] == b - a
    ph = np.zeros(n, bool); ph[1:n - 1] = (h[1:n - 1] > h[:n - 2]) & (h[1:n - 1] > h[2:])
    conf = np.zeros(n, bool); conf[1:] = ph[:-1]; rows = []; T = M + N + 1
    for b in np.flatnonzero(ph):
        t = b + 1
        if b < 1 or t + 1 + T >= n or not allok(b - 1, t + 1 + T) or np.isnan(atr[t]) or atr[t] <= 0: continue
        f = sweep(h, c, t + 1, h[b])
        if np.isnan(f): continue
        u = h[b] - c[t]
        lo, hi = max(t - WIN * slots, 1), min(t + WIN * slots, n - T - 2)
        cand = np.arange(lo, hi + 1); cand = cand[(cand - t) % slots == 0]; cand = cand[cand != t]
        cand = cand[~conf[cand] & ok[cand] & ~np.isnan(atrr[cand])]
        a0 = atrr[t]; cand = cand[(atrr[cand] <= a0 * 1.25) & (atrr[cand] >= a0 / 1.25)]
        cand = cand[np.array([allok(k2 + 1, k2 + 1 + T) for k2 in cand], bool)] if len(cand) else cand
        if len(cand) == 0: continue
        cs = rng.choice(cand, min(NC, len(cand)), replace=False)
        cv = [sweep(h, c, k2 + 1, c[k2] + u) for k2 in cs]; cv = [v for v in cv if not np.isnan(v)]
        rows.append((int((idx[t] + 1) * tf * 60000), u / atr[t], f, np.mean(cv) if cv else np.nan))
    return rows


def coin(path, rng):
    sym = os.path.basename(path).split(".")[0]; out = []
    for name, tf in TFS.items():
        idx, h, l, c, ok = G.bars(path, tf)
        for sign, dn in [(1, "high"), (-1, "low")]:
            for r in side(idx, h, l, c, ok, tf, rng, sign):
                out.append((r[0], sym, name, f"def.sweep_k1:{dn}") + r[1:])
    return out


if __name__ == "__main__":
    src, out = sys.argv[1], sys.argv[2]
    fs = [f"{src}/{s}.csv.gz" for s in sys.argv[3:]] or sorted(glob.glob(src + "/*.csv.gz"))
    rng = np.random.default_rng(458)
    cols = ["ts_utc_ms", "coin", "tf", "def_id", "u_atr", "f0", "c0"]
    pd.DataFrame([r for f in fs for r in coin(f, rng)], columns=cols).to_csv(out + "-events.csv.gz", index=False)
