#!/usr/bin/env python3
"""TK-443 (материал №5, без стакана; копия TK-442 + новые признаки; метка/база/бутстреп без изменений). Новое: q15 = оборот 15 м к норме 7 сут,
q1h; кратности ×10/×30/×100 с vr≥3 и без; round (V6/V7): расстояние до круглой цены d·10^k (1 знач. цифра) в б.п., «полка» = диапазон 30 м ≤ 30 б.п.;
час/фандинг (V8): минуты часа и до/после фандинга Bybit 00/08/16 UTC. Сравнение — vr>=3&qr>=3 (S10 TK-442).
TK-442: сигнал «щас будет движение» — признаки в момент t (только свечи ≤ t) против хода вперёд. Сетка 5 мин, кубы TK-436.
  python3 tk442-forward.py <куб> <месяц YYYY-MM> <вых.csv> [<корни OI через запятую>]
Метка (EV-W, ход от c[t]): up(t,D)=max c[s]/c[t]−1, s∈[t+90, t+D] (D=90: одна точка c[t+90]); dn(t,D)=1−min c[s]/c[t]; any=up или dn.
X_D(знак) = q99 ЭТОЙ меры своего месяца по всем (инструмент, t) месяца (не значения v11) → база ≈ 1 % по построению.
Шаг 2: oz = ln(OI[t]/OI[t−60м]) / σ(тех же 60-мин приращений за 7 сут, кончающихся в t−60) — OI Bybit 5 мин; S10 (накопление, TK-438: объём и
оборот перед стартом выше нормы) = vr≥a & qr≥b (сетка vr×qr). База считается и на подмножестве, где сигнал определён (haveS для sf, OI для oz);
интервалы — бутстреп по кластерам монета-сутки (B=200, seed 442). Признаки (ratio к норме 7 сут, кончающейся за 60 мин
до t): vr = σ1м(60м)/σ(7с), qr = объём(60м)/объём(7с), sf = корр. 1м-доходностей фьюч↔спот (60м), bv = vr у BTCUSDT, r60 = доход. 60м/(σ(7с)√60).
Сетки порогов — в SIG ниже, заморожены до счёта. Бэкфилл close вперёд (нет свечи = нет сделок)."""
import os
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


def load_oi(roots, syms, lo, T):
    """OI 5 мин → матрица [n,T] по минутам: последний отсчёт ≤ t, не старше 30 мин; NaN иначе."""
    out = np.full((len(syms), T), np.nan, np.float32)
    tm = (lo + np.arange(T, dtype=np.int64)) * MIN
    for k, s in enumerate(syms):
        tt, vv = [], []
        for r in roots:
            fn = f"{r}/{s}.csv.gz"
            if not os.path.exists(fn):
                continue
            try:
                d = np.loadtxt(fn, delimiter=",", skiprows=1, usecols=(0, 1), ndmin=2)
            except Exception:
                continue
            if d.size:
                tt.append(d[:, 0].astype(np.int64))
                vv.append(d[:, 1])
        if not tt:
            continue
        tt, vv = np.concatenate(tt), np.concatenate(vv)
        o = np.argsort(tt, kind="stable")
        tt, vv = tt[o], vv[o]
        g = vv > 0
        tt, vv = tt[g], vv[g]
        if not len(tt):
            continue
        ix = np.searchsorted(tt, tm, "right") - 1
        okk = (ix >= 0) & (tm - tt[np.maximum(ix, 0)] <= 30 * MIN)
        out[k, okk] = vv[ix[okk]]
    return out


def oi_z(L, ts):
    """L=ln OI [n,T]; сетка 5 мин (индексы кратны 5); норма — 7 сут 60-мин приращений, кончающихся в t−60; NaN, если норма < 50 % отсчётов."""
    n, T = L.shape
    ta = np.arange(0, T, STEP)
    X = np.full((n, len(ta)), np.nan)
    j0 = W // STEP
    X[:, j0:] = L[:, ta[j0:]] - L[:, ta[:-j0]]
    fin = np.isfinite(X)
    X0 = np.where(fin, X, 0.0)
    c2, cn = csum(X0 * X0), csum(fin.astype(np.float64))
    NW = NORM // STEP
    j = ts // STEP
    je = j - j0
    a2 = c2[:, je + 1] - c2[:, je + 1 - NW]
    an = cn[:, je + 1] - cn[:, je + 1 - NW]
    with np.errstate(all="ignore"):
        sd = np.sqrt(a2 / an)
        z = X[:, j] / sd
    z[(an < NW * 0.5) | ~(sd > 0)] = np.nan
    return z


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


def main(cube, ym, out, oi_roots=""):
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
    # TK-443 новые признаки
    q15 = rsum(Cq, i, 15) / 15 / q7
    ci = c[:, i]
    ex = np.floor(np.log10(ci))
    mant = ci / 10.0 ** ex
    near = np.minimum(np.abs(mant / np.round(mant) - 1), np.abs(mant / 10 - 1))
    near = np.where(mant >= 9.5, np.abs(mant / 10 - 1), near)
    rb = near * 1e4                                         # б.п. до ближайшей круглой цены d·10^k
    Hh, Ll = np.array(A[keep, 1, :]), np.array(A[keep, 2, :])
    hmx = np.full(ci.shape, -np.inf)
    lmn = np.full(ci.shape, np.inf)
    for k_ in range(30):
        hmx = np.fmax(hmx, Hh[:, i - k_])
        lmn = np.fmin(lmn, Ll[:, i - k_])
    rng30 = (hmx - lmn) / ci * 1e4                          # диапазон 30 м, б.п.
    tm_ = (lo + i) % 1440
    mh = np.broadcast_to((tm_ % 60)[None, :], ci.shape)
    fm = np.broadcast_to(((tm_ % 480))[None, :], ci.shape)  # минут после фандинга (00/08/16 UTC)
    SIG = {}
    for t in (3, 10, 30, 100):
        SIG[f"q15>={t}"] = q15 >= t
        SIG[f"q15>={t}&vr>=3"] = (q15 >= t) & (vr >= 3)
    for t in (10, 30, 100):
        SIG[f"qr>={t}"] = qr >= t
        SIG[f"qr>={t}&vr>=3"] = (qr >= t) & (vr >= 3)
    SIG["vr>=3&qr>=3"] = (vr >= 3) & (qr >= 3)
    for t in (3, 10):
        SIG[f"vr>=3&qr>=3&q15>={t}"] = SIG["vr>=3&qr>=3"] & (q15 >= t)
    for t in (10, 25, 50):
        SIG[f"round<={t}bp"] = rb <= t
        SIG[f"round<={t}bp&vr>=3&qr>=3"] = (rb <= t) & SIG["vr>=3&qr>=3"]
        SIG[f"shelf30m<=30bp&round<={t}bp"] = (rng30 <= 30) & (rb <= t)
        SIG[f"shelf30m<=30bp&round<={t}bp&qr>=3"] = (rng30 <= 30) & (rb <= t) & (qr >= 3)
    SIG["shelf30m<=30bp"] = rng30 <= 30
    SIG["hour:55-04"] = (mh >= 55) | (mh <= 4)
    SIG["hour:55-04&vr>=3&qr>=3"] = SIG["hour:55-04"] & SIG["vr>=3&qr>=3"]
    SIG["fund:-30..0"] = fm >= 450
    SIG["fund:0..+30"] = fm < 30
    SIG["fund:-30..+30&vr>=3&qr>=3"] = ((fm >= 450) | (fm < 30)) & SIG["vr>=3&qr>=3"]
    SIG["fund:0..+30&vr>=3&qr>=3"] = (fm < 30) & SIG["vr>=3&qr>=3"]
    SIG["fund:-30..0&vr>=3&qr>=3"] = (fm >= 450) & SIG["vr>=3&qr>=3"]
    oz = np.full(vr.shape, np.nan)
    okO = np.isfinite(oz)
    for k in SIG:
        SIG[k] = SIG[k] & nz
    days = (len(ok_t) * STEP) / 1440
    rows = []
    rng = np.random.default_rng(442)
    dayi = ((lo + ok_t) // 1440).astype(np.int64)
    dayi -= dayi.min()
    cid_full = np.arange(n, dtype=np.int64)[:, None] * (int(dayi.max()) + 1) + dayi[None, :]
    BB = 200
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
        uc, inv = np.unique(cid_full[valid], return_inverse=True)
        cidc = np.zeros(valid.shape, np.int64)
        cidc[valid] = inv
        NC = len(uc)
        Wt = rng.multinomial(NC, np.full(NC, 1.0 / NC), size=BB).astype(np.float32)
        hS = haveS[:, None]
        EV = {"": valid, "S": valid & hS, "O": valid & okO, "SO": valid & hS & okO}
        DEN = {}
        for en, ev in EV.items():
            nv_c = np.bincount(cidc[ev], minlength=NC).astype(np.float32)
            DEN[en] = (ev, int(ev.sum()), Wt @ nv_c,
                       {dr: (int((lb & ev).sum()), Wt @ np.bincount(cidc[lb & ev], minlength=NC).astype(np.float32))
                        for dr, lb in L.items()})
        for nm, sig in SIG.items():
            sg = sig & valid
            nf = int(sg.sum())
            en = ("S" if "sf" in nm else "") + ("O" if "oz" in nm else "")
            ev, nev, dnb, dpos = DEN[en]
            nfb = Wt @ np.bincount(cidc[sg], minlength=NC).astype(np.float32) if nf else None
            for dr, lab in L.items():
                npos = int(lab.sum())
                hit = sg & lab
                nh = int(hit.sum())
                npos_s, posb = dpos[dr]
                base_s = npos_s / max(nev, 1)
                if nh:
                    nhb = Wt @ np.bincount(cidc[hit], minlength=NC).astype(np.float32)
                    with np.errstate(all="ignore"):
                        pb = nhb / nfb
                        lb_ = pb / (posb / dnb)
                    plo, phi = np.nanpercentile(pb, [2.5, 97.5])
                    llo, lhi = np.nanpercentile(lb_, [2.5, 97.5])
                else:
                    plo = phi = llo = lhi = float("nan")
                kk, tt = np.nonzero(hit)
                cd = len(np.unique(kk.astype(np.int64) * 100000 + (ok_t[tt] // 1440))) if nh else 0
                base = npos / max(int(valid.sum()), 1)
                prec = nh / nf if nf else float("nan")
                rows.append((ym, nm, D, dr, (round(X[dr], 5) if dr != "any" else ""), int(valid.sum()), npos,
                             round(base, 5), nf, nh, round(prec, 5), round(nh / npos, 5) if npos else float("nan"),
                             round(prec / base, 3) if nf and base else float("nan"), round(nf / days / max(n, 1), 4), cd,
                             round(base_s, 5), round(nh / nf / base_s, 3) if nf and base_s else float("nan"),
                             round(plo, 5), round(phi, 5), round(llo, 3), round(lhi, 3)))
        print(ym, D, X, flush=True)
    with open(out, "w", newline="") as f:
        f.write("месяц,сигнал,D,направление,X_q99,ячеек,ячеек_с_ходом,база,срабатываний,попаданий,точность,полнота,подъём,срабат_в_сутки_на_монету,монета_дней_попаданий,база_подмн,подъём_подмн,точн_lo,точн_hi,подъём_lo,подъём_hi\n")
        for rw in rows:
            f.write(",".join(str(x) for x in rw) + "\n")
    print("rows", len(rows), "n", n, "days", days)


if __name__ == "__main__":
    main(*sys.argv[1:5])
