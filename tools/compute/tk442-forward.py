#!/usr/bin/env python3
"""TK-442: сигнал «щас будет движение» — признаки в момент t (только свечи ≤ t) против хода вперёд. Сетка 5 мин, кубы TK-436.
  python3 tk442-forward.py <куб> <месяц YYYY-MM> <вых.csv>
Метка (EV-W, ход от c[t]): up(t,D)=max c[s]/c[t]−1, s∈[t+90, t+D]; dn(t,D)=1−min c[s]/c[t]; any=up или dn.
X_D(знак) = q99 своего месяца по всем (инструмент, t) месяца → база ≈ 1 % по построению. Признаки (ratio к норме 7 сут, кончающейся за 60 мин
до t): vr = σ1м(60м)/σ(7с), qr = объём(60м)/объём(7с), sf = корр. 1м-доходностей фьюч↔спот (60м), bv = vr у BTCUSDT, r60 = доход. 60м/(σ(7с)√60).
Сетки порогов — в SIG ниже, заморожены до счёта. Бэкфилл close вперёд (нет свечи = нет сделок)."""
import re
import sys
import datetime

import numpy as np
from numba import njit

MIN = 60000
STEP = 5
DS = (90, 180, 360, 720, 1440)
W = 60
NORM = 7 * 1440
LEV = re.compile(r"(\d+[LS]|UP|DOWN|BULL|BEAR)USDT$")


def mb(ym):
    y, m = int(ym[:4]), int(ym[5:])
    a = datetime.datetime(y, m, 1, tzinfo=datetime.timezone.utc)
    b = datetime.datetime(y + (m == 12), m % 12 + 1, 1, tzinfo=datetime.timezone.utc)
    return int(a.timestamp()) // 60, int(b.timestamp()) // 60


def load(d, sub):
    s = open(f"{d}/{sub}.syms").read().split()
    lo = int(open(f"{d}/{sub}.lo").read().split()[0])
    return s, lo, np.load(f"{d}/{sub}.npy", mmap_mode="r")


def ffill_c(c):
    n, T = c.shape
    idx = np.where(np.isfinite(c), np.arange(T), 0)
    np.maximum.accumulate(idx, axis=1, out=idx)
    out = np.take_along_axis(c, idx, axis=1)
    first = np.argmax(np.isfinite(c), axis=1)
    out[np.arange(T)[None, :] < first[:, None]] = np.nan
    return out


def csum(x):
    z = np.zeros((x.shape[0], x.shape[1] + 1))
    np.cumsum(x, axis=1, out=z[:, 1:])
    return z


def rsum(cs, i, w):
    return cs[:, i + 1] - cs[:, i + 1 - w]


@njit(cache=True)
def fwd(c, a, b, sgn):
    """res[j] = max (sgn>0) / min (sgn<0) c[j+a..j+b] (монотонная очередь); NaN, где j+b ≥ T."""
    T = len(c)
    res = np.full(T, np.nan)
    dq = np.empty(T, np.int64)
    h = 0
    t = 0
    nxt = 0
    for j in range(T - b):
        while nxt <= j + b:
            v = c[nxt]
            while t > h and (c[dq[t - 1]] <= v if sgn > 0 else c[dq[t - 1]] >= v):
                t -= 1
            dq[t] = nxt
            t += 1
            nxt += 1
        while dq[h] < j + a:
            h += 1
        res[j] = c[dq[h]]
    return res


def main(cube, ym, out):
    m0, m1 = mb(ym)
    sy, lo, A = load(cube, "bybit-linear")
    ss, slo, S = load(cube, "bybit-spot")
    keep = [i for i, s in enumerate(sy) if s.endswith("USDT") and not LEV.search(s)]
    sy = [sy[i] for i in keep]
    T = A.shape[2]
    c = ffill_c(np.array(A[keep, 3, :]))
    q = np.nan_to_num(np.array(A[keep, 4, :]))
    sp = {s: i for i, s in enumerate(ss)}
    cs_ = np.full_like(c, np.nan)
    sidx = [(k, sp[s]) for k, s in enumerate(sy) if s in sp]
    if sidx:
        sc = ffill_c(np.array(S[[j for _, j in sidx], 3, :]))
        cs_[[k for k, _ in sidx]] = sc
    assert slo == lo
    lc = np.log(c)
    r = np.zeros_like(lc)
    r[:, 1:] = np.nan_to_num(np.diff(lc, axis=1))
    rs = np.zeros_like(lc)
    ls = np.log(cs_)
    rs[:, 1:] = np.nan_to_num(np.diff(ls, axis=1))
    haveS = np.isfinite(cs_).any(axis=1)
    Cr2, Cq = csum(r * r), csum(q)
    Cs2, Crs, Cr, Cs = csum(rs * rs), csum(r * rs), csum(r), csum(rs)
    present = csum(np.isfinite(A[keep, 3, :]).astype(np.float64))
    g0 = max(m0 - lo, NORM + W)
    g0 += (-g0) % STEP
    ts = np.arange(g0, m1 - lo, STEP)
    ok_t = ts[ts + 90 < T]
    n = len(sy)
    i = ok_t
    s60 = np.sqrt(rsum(Cr2, i, W) / W)
    ie = i - W                                      # норма: 7 сут, кончающиеся в t−60
    s7 = np.sqrt((Cr2[:, ie + 1] - Cr2[:, ie + 1 - NORM]) / NORM)
    q60 = rsum(Cq, i, W) / W
    q7 = (Cq[:, ie + 1] - Cq[:, ie + 1 - NORM]) / NORM
    with np.errstate(all="ignore"):
        vr = s60 / s7
        qr = q60 / q7
        sx, sy2 = rsum(Cr, i, W), rsum(Cs, i, W)
        cov = rsum(Crs, i, W) - sx * sy2 / W
        vx = rsum(Cr2, i, W) - sx * sx / W
        vy = rsum(Cs2, i, W) - sy2 * sy2 / W
        sf = cov / np.sqrt(vx * vy)
        sf[~haveS] = np.nan
        r60 = (lc[:, i] - lc[:, i - W]) / (s7 * np.sqrt(W))
    nz = (rsum(present, i, W) >= 30) & (present[:, ie + 1] - present[:, ie + 1 - NORM] >= NORM * 0.5) & (s7 > 0) & (q7 > 0)
    btc = sy.index("BTCUSDT")
    bv = np.broadcast_to(vr[btc], vr.shape)
    F = dict(vr=vr, qr=qr, sf=sf, bv=bv, r60=r60)
    # сетки порогов (заморожены): одиночные
    SIG = {}
    for t in (1.5, 2, 3):
        SIG[f"vr>={t}"] = F["vr"] >= t
    for t in (1.5, 2, 3, 5):
        SIG[f"qr>={t}"] = F["qr"] >= t
    for t in (0.5, 0.7, 0.9):
        SIG[f"sf>={t}"] = F["sf"] >= t
    for t in (1.5, 2):
        SIG[f"bv>={t}"] = F["bv"] >= t
    for t in (2, 3):
        SIG[f"r60>={t}"] = F["r60"] >= t       # направленные: вверх / вниз
        SIG[f"r60<=-{t}"] = F["r60"] <= -t
    for a in (1.5, 2, 3):
        for b in (1.5, 2, 3, 5):
            SIG[f"vr>={a}&qr>={b}"] = SIG[f"vr>={a}"] & SIG[f"qr>={b}"]
        for b in (0.5, 0.7, 0.9):
            SIG[f"vr>={a}&sf>={b}"] = SIG[f"vr>={a}"] & SIG[f"sf>={b}"]
            for qq in (2, 3):
                SIG[f"vr>={a}&qr>={qq}&sf>={b}"] = SIG[f"vr>={a}"] & SIG[f"qr>={qq}"] & SIG[f"sf>={b}"]
    for k in SIG:
        SIG[k] = SIG[k] & nz
    days = (len(ok_t) * STEP) / 1440
    rows = []
    for D in DS:
        L = {}
        cc = c[:, ok_t]
        okD = ok_t + D < T
        mx = np.full((n, len(ok_t)), np.nan)
        mn = np.full_like(mx, np.nan)
        for k in range(n):
            ex = fwd(c[k], 90, D, 1)
            ey = fwd(c[k], 90, D, -1)
            mx[k], mn[k] = ex[ok_t], ey[ok_t]
        with np.errstate(all="ignore"):
            up, dn = mx / cc - 1, 1 - mn / cc
        valid = nz & np.isfinite(up) & np.isfinite(dn)
        X = {}
        for nm, v in (("up", up), ("dn", dn)):
            X[nm] = float(np.quantile(v[valid], 0.99))
        L["up"] = valid & (up >= X["up"])
        L["dn"] = valid & (dn >= X["dn"])
        L["any"] = L["up"] | L["dn"]
        for nm, sig in SIG.items():
            sg = sig & valid
            nf = int(sg.sum())
            for dr, lab in L.items():
                npos = int(lab.sum())
                hit = sg & lab
                nh = int(hit.sum())
                kk, tt = np.nonzero(hit)
                cd = len(np.unique(kk.astype(np.int64) * 100000 + (ok_t[tt] // 1440))) if nh else 0
                base = npos / max(int(valid.sum()), 1)
                prec = nh / nf if nf else float("nan")
                rows.append((ym, nm, D, dr, (round(X[dr], 5) if dr != "any" else ""), int(valid.sum()), npos,
                             round(base, 5), nf, nh, round(prec, 5), round(nh / npos, 5) if npos else float("nan"),
                             round(prec / base, 3) if nf and base else float("nan"), round(nf / days / max(n, 1), 4), cd))
        print(ym, D, X, flush=True)
    with open(out, "w", newline="") as f:
        f.write("месяц,сигнал,D,направление,X_q99,ячеек,ячеек_с_ходом,база,срабатываний,попаданий,точность,полнота,подъём,срабат_в_сутки_на_монету,монета_дней_попаданий\n")
        for rw in rows:
            f.write(",".join(str(x) for x in rw) + "\n")
    print("rows", len(rows), "n", n, "days", days)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
