"""TK-458 B2 G7: события G3.1 на H1 + флаги d1 (последний D1-фрактал противоположного типа) и imb (незакрытая щель-цель за 50 H1-баров). Протокол P-TK458-b2-g7.
python -I tools/bp_g7.py <каталог csv.gz> <выход-префикс> [SYM ...]"""
import sys, glob, os, importlib.util
import numpy as np, pandas as pd

_s = importlib.util.spec_from_file_location("bp_g3", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bp_g3.py"))
B = importlib.util.module_from_spec(_s); _s.loader.exec_module(B)
G = B.G; NC, WIN, M, N = G.NC, G.WIN, B.M, B.N


def sweep_s(h, c, s0, lvl):
    a = np.flatnonzero(h[s0:s0 + M] > lvl)
    if len(a) == 0: return -1, np.nan
    s = s0 + a[0]; return s, float(c[s + N] < lvl)


def d1_fractals(path):
    """-> (время подтверждения мс, тип +1 хай/-1 лой) по возрастанию времени подтверждения"""
    idx, h, l, c, ok = G.bars(path, 1440); n = len(h); ct = []; ty = []
    for b in range(1, n - 1):
        if not (ok[b - 1] and ok[b] and ok[b + 1]): continue
        hi = h[b] > h[b - 1] and h[b] > h[b + 1]; lo = l[b] < l[b - 1] and l[b] < l[b + 1]
        if hi == lo: continue
        ct.append((idx[b + 1] + 1) * 86400000); ty.append(1 if hi else -1)
    return np.array(ct), np.array(ty)


def side(idx, h, l, c, ok, rng, sign, ct, ty):
    tf = 60
    if sign < 0: h, l, c = -l, -h, -c
    n = len(h); atr = G.atr14(h, l, c); slots = 1440 // tf
    atrr = atr / np.where(c != 0, np.abs(c), np.nan)
    okc = np.r_[0, np.cumsum(ok)]
    def allok(a, b): return okc[b] - okc[a] == b - a
    ph = np.zeros(n, bool); ph[1:n - 1] = (h[1:n - 1] > h[:n - 2]) & (h[1:n - 1] > h[2:])
    conf = np.zeros(n, bool); conf[1:] = ph[:-1]; rows = []; T = M + N + 1
    gap = np.zeros(n, bool); gap[:n - 2] = (l[2:] > h[:-2])
    for b in np.flatnonzero(ph):
        t = b + 1
        if b < 1 or t + 1 + T >= n or not allok(b - 1, t + 1 + T) or np.isnan(atr[t]) or atr[t] <= 0: continue
        s, f = sweep_s(h, c, t + 1, h[b])
        if s < 0: continue
        u = h[b] - c[t]
        k = np.searchsorted(ct, (idx[s] + 1) * 3600000, side="right") - 1
        d1 = int(k >= 0 and ty[k] == -sign)  # снятие хая (sign=+1) -> последний D1-фрактал лой (-1)
        imb = 0
        for i in range(max(s - 52, 0), s - 2):  # i+2 <= s-1, окно 50 H1-баров до s
            if i + 2 < s - 50: continue
            if gap[i] and allok(i, s) and l[i + 2] < h[b] and (s - 1 < i + 3 or l[i + 3:s].min() > h[i]): imb = 1; break
        lo, hi = max(t - WIN * slots, 1), min(t + WIN * slots, n - T - 2)
        cand = np.arange(lo, hi + 1); cand = cand[(cand - t) % slots == 0]; cand = cand[cand != t]
        cand = cand[~conf[cand] & ok[cand] & ~np.isnan(atrr[cand])]
        a0 = atrr[t]; cand = cand[(atrr[cand] <= a0 * 1.25) & (atrr[cand] >= a0 / 1.25)]
        cand = cand[np.array([allok(k2 + 1, k2 + 1 + T) for k2 in cand], bool)] if len(cand) else cand
        if len(cand) == 0: continue
        cs = rng.choice(cand, min(NC, len(cand)), replace=False)
        cv = [B.sweep(h, c, k2 + 1, c[k2] + u) for k2 in cs]; cv = [v for v in cv if not np.isnan(v)]
        rows.append((int((idx[t] + 1) * tf * 60000), u / atr[t], f, np.mean(cv) if cv else np.nan, d1, imb))
    return rows


def coin(path, rng):
    sym = os.path.basename(path).split(".")[0]; out = []
    idx, h, l, c, ok = G.bars(path, 60); ct, ty = d1_fractals(path)
    for sign, dn in [(1, "high"), (-1, "low")]:
        for r in side(idx, h, l, c, ok, rng, sign, ct, ty):
            out.append((r[0], sym, "H1", f"def.sweep_k1:{dn}") + r[1:])
    return out


if __name__ == "__main__":
    src, out = sys.argv[1], sys.argv[2]
    fs = [f"{src}/{s}.csv.gz" for s in sys.argv[3:]] or sorted(glob.glob(src + "/*.csv.gz"))
    rng = np.random.default_rng(458)
    cols = ["ts_utc_ms", "coin", "tf", "def_id", "u_atr", "f0", "c0", "d1", "imb"]
    pd.DataFrame([r for f in fs for r in coin(f, rng)], columns=cols).to_csv(out + "-events.csv.gz", index=False)
