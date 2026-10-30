"""TK-458 B2 шаг G3.2/G3.3: касание W1/D1-фрактала и PDH/PDL -> разворот close[s+10] < уровня, контроль C1. Протокол docs/research/P-TK458-b2-g32-2026-10-30.md.
python -I tools/bp_g32.py <каталог csv.gz> <выход-префикс> [SYM ...]"""
import sys, glob, os, importlib.util
import numpy as np, pandas as pd

_s = importlib.util.spec_from_file_location("bp_g4", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bp_g4.py"))
G = importlib.util.module_from_spec(_s); _s.loader.exec_module(G)
NC, WIN = G.NC, G.WIN; M = 50; N = 10; MON = 4 * 86400000  # понедельник = эпоха + 4 сут


def bars(d, tf, off=0):
    g = (d.t - off) // (tf * 60000)
    a = d.groupby(g, sort=True).agg(h=("h", "max"), l=("l", "min"), c=("c", "last"), n=("t", "size"))
    a = a.reindex(pd.RangeIndex(a.index.min(), a.index.max() + 1))
    return dict(st=a.index.to_numpy() * tf * 60000 + off, h=a.h.to_numpy(), l=a.l.to_numpy(), c=a.c.to_numpy(), ok=(a.n == tf).fillna(False).to_numpy(), tf=tf)


def flip(B):
    return dict(B, h=-B["l"], l=-B["h"], c=-B["c"])


def events(H, Mb, mode, rng):
    """H — уровневый ТФ, Mb — измерительный; mode: frac | pd. Возвращает (t_мс, u/atr, f, c)"""
    h, l, c, ok = Mb["h"], Mb["l"], Mb["c"], Mb["ok"]; n = len(h); tf = Mb["tf"]; slots = 1440 // tf
    atr = G.atr14(h, l, c); atrr = atr / np.where(c != 0, np.abs(c), np.nan)
    okc = np.r_[0, np.cumsum(ok)]
    def allok(a, b): return okc[b] - okc[a] == b - a
    hh, hok = H["h"], H["ok"]; nh = len(hh); rows = []
    if mode == "frac":
        bs = [b for b in range(1, nh - 1) if hok[b - 1] and hok[b] and hok[b + 1] and hh[b] > hh[b - 1] and hh[b] > hh[b + 1]]
        t0s = [H["st"][b + 2] if b + 2 < nh else H["st"][b + 1] + H["tf"] * 60000 for b in bs]
        lv = [hh[b] for b in bs]; wm = M * H["tf"] // tf
    else:
        bs = [b for b in range(0, nh - 1) if hok[b]]
        t0s = [H["st"][b + 1] for b in bs]; lv = [hh[b] for b in bs]; wm = 24
    for t0, L in zip(t0s, lv):
        s0 = int(np.searchsorted(Mb["st"], t0))
        if s0 < 2 or s0 + wm + N + 1 >= n: continue
        a = np.flatnonzero(h[s0:s0 + wm] > L)
        if len(a) == 0: continue
        s = s0 + int(a[0])
        if s + N >= n or not allok(s - 1, s + N + 1) or np.isnan(atr[s - 1]) or atr[s - 1] <= 0: continue
        u = L - c[s - 1]
        if u <= 0: continue
        f = float(c[s + N] < L)
        lo, hi = max(s - WIN * slots, 2), min(s + WIN * slots, n - N - 2)
        cand = np.arange(lo, hi + 1); cand = cand[(cand - s) % slots == 0]; cand = cand[cand != s]
        cand = cand[ok[cand - 1] & ~np.isnan(atrr[cand - 1])]
        a0 = atrr[s - 1]; cand = cand[(atrr[cand - 1] <= a0 * 1.25) & (atrr[cand - 1] >= a0 / 1.25)]
        if len(cand): cand = cand[h[cand] > c[cand - 1] + u]
        if len(cand): cand = cand[np.array([allok(k - 1, k + N + 1) for k in cand], bool)]
        cv = np.nan
        if len(cand):
            cs = rng.choice(cand, min(NC, len(cand)), replace=False)
            cv = float(np.mean([c[k + N] < c[k - 1] + u for k in cs]))
        rows.append((int(Mb["st"][s] + tf * 60000), u / atr[s - 1], f, cv))
    return rows


def coin(path, rng):
    sym = os.path.basename(path).split(".")[0]; out = []
    d = pd.read_csv(path, usecols=[0, 1, 2, 3, 4]); d.columns = ["t", "o", "h", "l", "c"]
    W1, D1, H1 = bars(d, 10080, MON), bars(d, 1440), bars(d, 60)
    for nm, H, Mb, mode in [("G3.2 W1-фрактал→D1", W1, D1, "frac"), ("G3.3 D1-фрактал→H1", D1, H1, "frac"), ("G3.3 PDH/PDL→H1", D1, H1, "pd")]:
        for sign, dn in [(1, "high"), (-1, "low")]:
            Hs, Ms = (H, Mb) if sign > 0 else (flip(H), flip(Mb))
            for r in events(Hs, Ms, mode, rng): out.append((r[0], sym, nm, f"def.touch:{dn}") + r[1:])
    return out


if __name__ == "__main__":
    src, out = sys.argv[1], sys.argv[2]
    fs = [f"{src}/{s}.csv.gz" for s in sys.argv[3:]] or sorted(glob.glob(src + "/*.csv.gz"))
    rng = np.random.default_rng(458)
    cols = ["ts_utc_ms", "coin", "tf", "def_id", "u_atr", "f0", "c0"]
    pd.DataFrame([r for f in fs for r in coin(f, rng)], columns=cols).to_csv(out + "-events.csv.gz", index=False)
