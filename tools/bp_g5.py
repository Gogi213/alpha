"""TK-458 B2 шаг G5: снятие границы Азии и отработка (bpP-01), контроль — сдвиги +8/+16 ч. Протокол docs/research/P-TK458-b2-g5-2026-10-30.md.
python -I tools/bp_g5.py <каталог csv.gz> <выход-префикс> [SYM ...]"""
import sys, glob, os, datetime as dt
from zoneinfo import ZoneInfo
import numpy as np, pandas as pd

NY = ZoneInfo("America/New_York"); UTC = dt.timezone.utc
DEPTH = [0.25, 0.5, 1.0, np.inf]; R, T = 420, 540; SHIFTS = [0, 8, 16]


def minutes(path):
    d = pd.read_csv(path, usecols=[0, 2, 3]); d.columns = ["t", "h", "l"]
    m = d.t.values // 60000; m0 = int(m.min()); n = int(m.max()) - m0 + 1
    h = np.full(n, np.nan); l = np.full(n, np.nan); h[m - m0] = d.h.values; l[m - m0] = d.l.values
    return m0, h, l


def one(h, l, s):
    """s: индекс начала диапазона. -> None | (снято, [отработка по глубинам])"""
    if s < 0 or s + R + T > len(h): return None
    H, L = h[s:s + R + T], l[s:s + R + T]
    if np.isnan(H).any(): return None
    ah, al = H[:R].max(), L[:R].min(); w = ah - al
    if w <= 0: return None
    up, dn = H[R:] > ah, L[R:] < al; hit = np.flatnonzero(up | dn)
    if len(hit) == 0: return (0, [0] * 4)
    i = hit[0]
    if up[i] and dn[i]: return None
    j = R + i
    if up[i]:
        back = np.flatnonzero(L[j:] <= al); depth = (H[j:j + back[0] + 1].max() - ah) / w if len(back) else np.inf
    else:
        back = np.flatnonzero(H[j:] >= ah); depth = (al - L[j:j + back[0] + 1].min()) / w if len(back) else np.inf
    ok = len(back) > 0
    return (1, [int(ok and depth <= d) for d in DEPTH])


def coin(path):
    sym = os.path.basename(path).split(".")[0]; m0, h, l = minutes(path); rows = []
    for day in pd.date_range(pd.Timestamp(m0 * 60, unit="s").date(), pd.Timestamp((m0 + len(h)) * 60, unit="s").date()):
        b = (day - pd.Timedelta(days=1)).date()
        s0 = int(dt.datetime(b.year, b.month, b.day, 19, tzinfo=NY).astimezone(UTC).timestamp() // 60)
        r = [one(h, l, s0 - m0 + 60 * k) for k in SHIFTS]
        if r[0] is None or r[0][0] == 0: continue
        c = [x[1] for x in r[1:] if x is not None and x[0] == 1]
        if not c: continue
        rows.append((s0 * 60000, sym, "1m", "def.asia_range:bpP-01") + tuple(r[0][1]) + tuple(np.mean(c, axis=0)))
    return rows


if __name__ == "__main__":
    src, out = sys.argv[1], sys.argv[2]
    fs = [f"{src}/{s}.csv.gz" for s in sys.argv[3:]] or sorted(glob.glob(src + "/*.csv.gz"))
    cols = ["ts_utc_ms", "coin", "tf", "def_id"] + [f"f{i}" for i in range(4)] + [f"c{i}" for i in range(4)]
    pd.DataFrame([r for f in fs for r in coin(f)], columns=cols).to_csv(out + "-events.csv.gz", index=False)
