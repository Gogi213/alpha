"""TK-458 B2 G8.1: снятие Азии -> 5M-имбаланс -> вход по close; R нетто (стоп за экстремум, цели 2/1.8/1R), контроль - случайные входы. Протокол P-TK458-b2-g8.
python -I tools/bp_g8.py <каталог csv.gz> <выход-префикс> [SYM ...]"""
import sys, glob, os, datetime as dt
from zoneinfo import ZoneInfo
import numpy as np, pandas as pd

NY = ZoneInfo("America/New_York"); UTC = dt.timezone.utc
R, T = 420, 540; MS = [2.0, 1.8, 1.0]; FEE = 0.0005; NC = 5; WIN = 30; SLOTS = 288


def minutes(path):
    d = pd.read_csv(path, usecols=[0, 2, 3, 4]); d.columns = ["t", "h", "l", "c"]
    m = d.t.values // 60000; m0 = int(m.min()); n = int(m.max()) - m0 + 1
    h = np.full(n, np.nan); l = np.full(n, np.nan); c = np.full(n, np.nan)
    h[m - m0] = d.h.values; l[m - m0] = d.l.values; c[m - m0] = d.c.values
    off = (-m0) % 5  # выравнивание на границу M5 (минуты от эпохи кратны 5)
    return m0 + off, h[off:], l[off:], c[off:]


def m5(h, l, c):
    n = len(h) // 5; H = h[:n * 5].reshape(n, 5); L = l[:n * 5].reshape(n, 5); C = c[:n * 5].reshape(n, 5)
    bh, bl, bc = H.max(1), L.min(1), C[:, -1]
    pc = np.r_[np.nan, bc[:-1]]; tr = np.maximum(bh - bl, np.maximum(np.abs(bh - pc), np.abs(bl - pc)))
    return bh, bl, bc, pd.Series(tr).rolling(14).mean().to_numpy()


def payoff(hh, ll, cc, E, rho, sign):
    """минуты после входа (sign=-1 шорт: стоп E+rho, цель E-m*rho). -> R брутто по m; стоп и цель в одной минуте = стоп; тайм-аут по close"""
    out = []
    for m in MS:
        if sign < 0: stop = hh >= E + rho; tgt = ll <= E - m * rho
        else: stop = ll <= E - rho; tgt = hh >= E + m * rho
        a = np.flatnonzero(stop | tgt)
        out.append((-1.0 if stop[a[0]] else m) if len(a) else sign * (cc[-1] - E) / rho)
    return out


def event(h, l, c, bh, bl, bc, atr, s0):
    """s0 - начало диапазона в массиве минут. -> None | (sign, bar i, E, rho, atr_i, минута входа, минута конца окна)"""
    if s0 < 0 or s0 + R + T > len(h): return None
    H, L = h[s0:s0 + R + T], l[s0:s0 + R + T]
    if np.isnan(H).any() or np.isnan(L).any(): return None
    ah, al = H[:R].max(), L[:R].min()
    if ah <= al: return None
    up, dn = H[R:] > ah, L[R:] < al; hit = np.flatnonzero(up | dn)
    if len(hit) == 0: return None
    k = hit[0]
    if up[k] and dn[k]: return None
    sign = -1 if up[k] else 1; t_sw = s0 + R + k; t_end = s0 + R + T; b0 = t_sw // 5
    for i in range(max(b0 + 2, 14), t_end // 5):
        if np.isnan(atr[i]): continue
        g = (bl[i - 2] - bh[i]) if sign < 0 else (bl[i] - bh[i - 2])
        if g >= atr[i] / 4:
            t_cl = (i + 1) * 5; E = bc[i]
            ext = np.nanmax(h[t_sw:t_cl]) if sign < 0 else np.nanmin(l[t_sw:t_cl])
            rho = (ext - E) if sign < 0 else (E - ext)
            return None if (rho <= 0 or np.isnan(rho) or np.isnan(E)) else (sign, i, E, rho, atr[i], t_cl, t_end)
    return None


def clean(*a): return not any(np.isnan(x).any() for x in a)


def coin(path):
    sym = os.path.basename(path).split(".")[0]; m0, h, l, c = minutes(path)
    bh, bl, bc, atr = m5(h, l, c); n5 = len(bh); okb = ~np.isnan(atr)
    rng = np.random.default_rng(458); rows = []
    for day in pd.date_range(pd.Timestamp(m0 * 60, unit="s").date(), pd.Timestamp((m0 + len(h)) * 60, unit="s").date()):
        b = (day - pd.Timedelta(days=1)).date()
        s0 = int(dt.datetime(b.year, b.month, b.day, 19, tzinfo=NY).astimezone(UTC).timestamp() // 60) - m0
        e = event(h, l, c, bh, bl, bc, atr, s0)
        if e is None: continue
        sign, i, E, rho, a, t_cl, t_end = e
        hh, ll, cc = h[t_cl:t_end], l[t_cl:t_end], c[t_cl:t_end]
        if len(hh) < 1 or not clean(hh, ll, cc): continue
        rf = np.array(payoff(hh, ll, cc, E, rho, sign)) - 2 * FEE * E / rho
        dur = t_end - t_cl; ra = a / E; k_atr = rho / a
        cand = np.arange(max(i - WIN * SLOTS, 14), min(i + WIN * SLOTS, n5 - dur // 5 - 2))
        cand = cand[((cand - i) % SLOTS == 0) & (cand != i)]; cand = cand[okb[cand]]
        ar = atr[cand] / bc[cand]; cand = cand[(ar <= ra * 1.25) & (ar >= ra / 1.25)]
        cr = []
        for j in (rng.choice(cand, min(NC, len(cand)), replace=False) if len(cand) else []):
            tc = (j + 1) * 5; Ej = bc[j]; rj = k_atr * atr[j]
            H2, L2, C2 = h[tc:tc + dur], l[tc:tc + dur], c[tc:tc + dur]
            if len(H2) < dur or rj <= 0 or not clean(H2, L2, C2): continue
            cr.append(np.array(payoff(H2, L2, C2, Ej, rj, sign)) - 2 * FEE * Ej / rj)
        cm = np.mean(cr, axis=0) if cr else np.full(3, np.nan)
        rows.append(((m0 + s0) * 60000, sym, "5m", f"def.setup_g8:{'short' if sign < 0 else 'long'}", k_atr) + tuple(rf) + tuple(cm))
    return rows


if __name__ == "__main__":
    src, out = sys.argv[1], sys.argv[2]
    fs = [f"{src}/{s}.csv.gz" for s in sys.argv[3:]] or sorted(glob.glob(src + "/*.csv.gz"))
    cols = ["ts_utc_ms", "coin", "tf", "def_id", "risk_atr"] + [f"f{i}" for i in range(3)] + [f"c{i}" for i in range(3)]
    pd.DataFrame([r for f in fs for r in coin(f)], columns=cols).to_csv(out + "-events.csv.gz", index=False)
