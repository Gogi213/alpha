"""Сверка круга «через 250 мс» (8-е поле закрытых эпизодов state.pkl двойника) с независимой реализацией по сырым записям сборщиков.
Независимая часть — циклы по определению из разбора математики (arb-math-review-2026-11-15-scripts/indep.py: те же load / view / rows / centre_of / episodes / state_at),
кода проекта в счёте не использует. Эпизод демона и эпизод независимого счёта сопоставляются по началу (±2 мс: у выгрузки rx округлён, у прода — пол) и стороне.
Запуск на srv: python3 chk_net250.py <каталог с raw-<биржа>-<СИМВОЛ>.tsv> <state.pkl двойника> <монета> [L мс]"""
import pickle
import sys
from bisect import bisect_right
from statistics import median

import numpy as np

sys.path.insert(0, sys.argv[0].rsplit("/", 1)[0] if "/" in sys.argv[0] else ".")
D, STATE, COIN = sys.argv[1], sys.argv[2], sys.argv[3]
L = int(sys.argv[4]) if len(sys.argv) > 4 else 250
TH = FEE = 12.6
sys.path.insert(0, "/data/arb-settings/code/tools/arb_stats")
import arb_inc  # noqa: E402,F401  (распаковка state.pkl)


def load(ex):
    a = np.loadtxt(f"{D}/raw-{ex}-{COIN}.tsv", dtype=float, ndmin=2)
    return {"rx": a[:, 0].astype(np.int64), "lag": a[:, 1].astype(np.int64), "bid": a[:, 4], "ask": a[:, 6]}


def view(r):
    rx, lag, bid, ask = r["rx"], r["lag"], r["bid"], r["ask"]
    e = rx - lag
    o = np.lexsort((rx, e))
    t, b, a = e[o], bid[o], ask[o]
    ok = (b > 0) & (a > 0) & (b <= a)
    t, b, a = t[ok], b[ok], a[ok]
    keep = [0]
    for i in range(1, len(t)):
        if b[i] != b[keep[-1]] or a[i] != a[keep[-1]]:
            keep.append(i)
    k = np.array(keep)
    return t[k], b[k], a[k]


def rows(A, B):
    tA, bA, aA = A
    tB, bB, aB = B
    out = []
    for t in sorted(set(tA.tolist()) | set(tB.tolist())):
        i, j = bisect_right(tA, t) - 1, bisect_right(tB, t) - 1
        if i < 0 or j < 0:
            continue
        out.append((t, (bA[i] / aB[j] - 1) * 1e4, (aA[i] / bB[j] - 1) * 1e4))
    return out


def centre_of(R):
    close = {}
    for t, S, Bu in R:
        close[t // 60000] = (S + Bu) / 2
    cen, ks = {}, sorted(close)
    for k in range(ks[0], ks[-1] + 2):
        w = [close[m] for m in range(k - 60, k) if m in close]
        cen[k] = median(w) if len(w) >= 30 else None
    return cen


def episodes(R, cen):
    eps, st = [], {1: None, -1: None}
    for t, S, Bu in R:
        c = cen.get(t // 60000)
        if c is None:
            continue
        M = (S + Bu) / 2
        for d in (1, -1):
            e = S - c if d == 1 else c - Bu
            o = st[d]
            if o is None:
                if e >= TH:
                    st[d] = [t, S, Bu, e]
            else:
                o[3] = max(o[3], e)
                if (d == 1 and M <= c) or (d == -1 and M >= c):
                    eps.append((o[0], d, t - o[0], o[3], o[1] - Bu if d == 1 else S - o[2]))
                    st[d] = None
    return eps


def state_at(V, t):
    tt, b, a = V
    i = bisect_right(tt, t) - 1
    return (b[i], a[i]) if i >= 0 else (None, None)


W = pickle.load(open(STATE, "rb"))
t_cut = W.t_a - 10 * 60000
raw = {}
for ex in ("binance", "bybit", "bingx", "okx", "gate"):
    try:
        raw[ex] = load(ex)
    except OSError:
        pass
tot = []
for ea, eb in (("binance", "bybit"), ("binance", "bingx"), ("bybit", "bingx")):
    if ea not in raw or eb not in raw:
        continue
    A, B = view(raw[ea]), view(raw[eb])
    R = rows(A, B)
    t_lo = max(A[0][0], B[0][0]) + 90 * 60000
    mine = [x for x in episodes(R, centre_of(R)) if t_lo <= x[0] < t_cut]
    ps = W.pairs.get((COIN, ea, eb))
    dm = {}
    for x in ps.closed:
        if t_lo <= x[0] < t_cut and len(x) > 7 and x[7] is not None:
            for s_ in (-2, -1, 0, 1, 2):
                dm.setdefault((x[0] + s_, x[1]), x)
    dif, gm, gd = [], [], []
    for (ti, d, tau, pk, gross) in mine:
        x = dm.get((ti, d))
        if x is None:
            continue
        bA0, aA0 = state_at(A, ti + L)
        bB0, aB0 = state_at(B, ti + L)
        bA1, aA1 = state_at(A, ti + tau + L)
        bB1, aB1 = state_at(B, ti + tau + L)
        S0, B0 = (bA0 / aB0 - 1) * 1e4, (aA0 / bB0 - 1) * 1e4
        S1, B1 = (bA1 / aB1 - 1) * 1e4, (aA1 / bB1 - 1) * 1e4
        g = (S0 - B1) if d == 1 else (S1 - B0)
        dif.append(abs(g - x[7]))
        gm.append(g - FEE)
        gd.append(x[7] - FEE)
    dif = np.array(dif)
    print(f"{COIN} {ea}-{eb}: независимых эпизодов {len(mine)}, сопоставлено с демоном {len(dif)} ({100 * len(dif) / max(len(mine), 1):.1f} %)")
    if len(dif):
        print(f"  |круг через {L} мс: демон − независимый|: p50 {np.median(dif):.5f}  p99 {np.percentile(dif, 99):.4f}  max {dif.max():.4f} bps;  > 0,01 bps: {(dif > 0.01).sum()}")
        print(f"  после комиссий через {L} мс: независимый p50 {np.median(gm):.2f}, доля > 0 {100 * (np.array(gm) > 0).mean():.1f} %;  демон p50 {np.median(gd):.2f}, доля > 0 {100 * (np.array(gd) > 0).mean():.1f} %")
        tot.append(dif)
if tot:
    a = np.concatenate(tot)
    print(f"ВСЕГО сопоставлено {len(a)}; max |Δ| {a.max():.4f} bps; |Δ| > 0,01 bps: {(a > 0.01).sum()}")
