"""TK-458 B2 шаг G4.1: закрытие телом за имбалансом (FVG) -> «не отработан» (нет возврата за N=10), контроль C2 «фитиль сквозь щель». Протокол docs/research/P-TK458-b2-g41-2026-10-30.md.
python -I tools/bp_g41.py <каталог csv.gz> <выход-префикс> [SYM ...]   (выход совместим с bp_g4_agg.py)"""
import sys, glob, os, importlib.util
import numpy as np, pandas as pd

_s = importlib.util.spec_from_file_location("bp_g4", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bp_g4.py"))
G = importlib.util.module_from_spec(_s); _s.loader.exec_module(G)
N, NC, WIN, TFS = G.N, G.NC, G.WIN, G.TFS


def side(idx, h, l, c, ok, tf, rng, sign):
    if sign < 0: h, l, c = -l, -h, -c
    n = len(h); atr = G.atr14(h, l, c); slots = 1440 // tf
    atrr = atr / np.where(c != 0, np.abs(c), np.nan)
    gap = np.zeros(n, bool); gap[2:] = ok[2:] & ok[1:-1] & ok[:-2] & (l[2:] > h[:-2])
    # k = первый бар i+3..i+12, прошедший щель; тело/фитиль
    ev, ct = [], []   # события (k, i, j) и пул контроля (k')
    for j in np.flatnonzero(gap[:n - N - 14]) + 0:
        if j < 2: continue
        top = h[j - 2]; w = l[j + 1:j + 11]
        if len(w) < 10 or not ok[j + 1:j + 11].all(): continue
        hit = np.flatnonzero(w <= top)
        if len(hit) == 0: continue
        k = j + 1 + hit[0]
        if k + N >= n or not ok[k + 1:k + 1 + N].all(): continue
        (ev if c[k] < top else ct).append((k, j))
    if not ev or not ct: return []
    ctk = np.array([k for k, _ in ct]); rows = []
    for k, j in ev:
        if np.isnan(atr[j]) or atr[j] <= 0: continue
        g = l[j] - h[j - 2]; D = l[j] - c[k]
        ret = float(h[k + 1:k + 1 + N].max() >= l[j])
        t0 = k * 1; a0 = atrr[j]
        cand = ctk[(np.abs(ctk - k) <= WIN * slots) & ~np.isnan(atrr[ctk])]
        cand = cand[(atrr[cand] <= a0 * 1.25) & (atrr[cand] >= a0 / 1.25)]
        if len(cand) == 0: continue
        cs = rng.choice(cand, min(NC, len(cand)), replace=False)
        cr = np.mean([h[q + 1:q + 1 + N].max() >= c[q] + D for q in cs])
        rows.append((int((idx[k] + 1) * tf * 60000), g / atr[j], 1 - ret, 1 - ret, 1 - cr, 1 - cr))
    return rows


def coin(path, rng):
    sym = os.path.basename(path).split(".")[0]; out = []
    for name, tf in TFS.items():
        idx, h, l, c, ok = G.bars(path, tf)
        for sign, dn in [(1, "bull"), (-1, "bear")]:
            for r in side(idx, h, l, c, ok, tf, rng, sign):
                out.append((r[0], sym, name, f"def.imbalance+close:{dn}") + r[1:])
    return out


if __name__ == "__main__":
    src, out = sys.argv[1], sys.argv[2]
    fs = [f"{src}/{s}.csv.gz" for s in sys.argv[3:]] or sorted(glob.glob(src + "/*.csv.gz"))
    rng = np.random.default_rng(458)
    cols = ["ts_utc_ms", "coin", "tf", "def_id", "g_atr", "f_ful", "f_50", "c_ful", "c_50"]
    pd.DataFrame([r for f in fs for r in coin(f, rng)], columns=cols).to_csv(out + "-events.csv.gz", index=False)
