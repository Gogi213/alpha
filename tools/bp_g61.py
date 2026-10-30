"""TK-458 B2 шаг G6.1/G6.2: в какой сессии снята граница Азии; разворот на NY к противоположной границе. Контроль — сдвиги +8/+16 ч.
Протокол docs/research/P-TK458-b2-g61-2026-10-30.md. python -I tools/bp_g61.py <каталог csv.gz> <выход-префикс> [SYM ...]"""
import sys, glob, os, datetime as dt
from zoneinfo import ZoneInfo
import numpy as np, pandas as pd

NY = ZoneInfo("America/New_York"); UTC = dt.timezone.utc
R, T = 420, 540; SHIFTS = [0, 8, 16]; LON, NYS = (60, 360), (360, 540); T0 = 360


def minutes(path):
    d = pd.read_csv(path, usecols=[0, 2, 3]); d.columns = ["t", "h", "l"]
    m = d.t.values // 60000; m0 = int(m.min()); n = int(m.max()) - m0 + 1
    h = np.full(n, np.nan); l = np.full(n, np.nan); h[m - m0] = d.h.values; l[m - m0] = d.l.values
    return m0, h, l


def one(h, l, s):
    """-> None | None-снятия: (-1, ...) ; иначе (мин снятия, сторона +1/-1, [цель достигнута к T0 (0/1), цель достигнута в [T0,T) (0/1)])"""
    if s < 0 or s + R + T > len(h): return None
    H, L = h[s:s + R + T], l[s:s + R + T]
    if np.isnan(H).any(): return None
    ah, al = H[:R].max(), L[:R].min()
    if ah - al <= 0: return None
    up, dn = H[R:] > ah, L[R:] < al; hit = np.flatnonzero(up | dn)
    if len(hit) == 0: return (-1, 0, 0, 0)
    i = hit[0]
    if up[i] and dn[i]: return None
    side = 1 if up[i] else -1
    tgt = (L[R:] <= al) if side == 1 else (H[R:] >= ah)
    t = np.flatnonzero(tgt[i:]); tm = i + t[0] if len(t) else 10 ** 9
    return (int(i), side, int(tm < T0), int(T0 <= tm < T))


def feats(r):
    """r -> dict: g1L, g1N (0/1 | nan), g2u, g2d (исход 0/1 | nan)"""
    f = dict(L=np.nan, N=np.nan, u=np.nan, d=np.nan)
    if r is None or r[0] < 0: return f
    i, side, before, inwin = r
    f["L"] = float(LON[0] <= i < LON[1]); f["N"] = float(NYS[0] <= i < NYS[1])
    if i < T0 and not before: f["u" if side == 1 else "d"] = float(inwin)
    return f


def coin(path):
    sym = os.path.basename(path).split(".")[0]; m0, h, l = minutes(path); rows = []
    for day in pd.date_range(pd.Timestamp(m0 * 60, unit="s").date(), pd.Timestamp((m0 + len(h)) * 60, unit="s").date()):
        b = (day - pd.Timedelta(days=1)).date()
        s0 = int(dt.datetime(b.year, b.month, b.day, 19, tzinfo=NY).astimezone(UTC).timestamp() // 60)
        rs = [one(h, l, s0 - m0 + 60 * k) for k in SHIFTS]
        if rs[0] is None: continue
        F = [feats(r) for r in rs]; row = []
        for k in "LNud":
            c = [x[k] for x in F[1:] if not np.isnan(x[k])]
            row += [F[0][k], float(np.mean(c)) if c else np.nan]
        # G6.1 только по дням со снятием в факте; G6.2 — по NaN-осознанной паре
        if not np.isnan(row[0]) or not np.isnan(row[4]) or not np.isnan(row[6]):
            rows.append((s0 * 60000, sym, "1m", "def.asia_range:g61g62") + tuple(row))
    return rows


if __name__ == "__main__":
    src, out = sys.argv[1], sys.argv[2]
    fs = [f"{src}/{s}.csv.gz" for s in sys.argv[3:]] or sorted(glob.glob(src + "/*.csv.gz"))
    cols = ["ts_utc_ms", "coin", "tf", "def_id", "fL", "cL", "fN", "cN", "fu", "cu", "fd", "cd"]
    pd.DataFrame([r for f in fs for r in coin(f)], columns=cols).to_csv(out + "-events.csv.gz", index=False)
