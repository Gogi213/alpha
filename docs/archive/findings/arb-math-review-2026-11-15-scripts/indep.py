"""Independent re-implementation of the tab's fee-threshold episodes for EVAA pairs from raw collector records, plus
time-base (E vs receive), staleness and execution-delay checks. Plain loops, no project code for the computation."""
import pickle
import sys
from bisect import bisect_right
from statistics import median

import numpy as np

sys.path.insert(0, "/opt/arb-tab/tools/arb_stats")
import arb_inc  # noqa: E402,F401  (for unpickling)

TH, FEE = 12.6, 12.6
D = "/tmp/arbreview"


def load(ex):
    a = np.loadtxt(f"{D}/raw-{ex}-EVAA.tsv", dtype=float, ndmin=2)
    return {"rx": a[:, 0].astype(np.int64), "lag": a[:, 1].astype(np.int64), "bid": a[:, 4], "ask": a[:, 6]}


def view(r, base):
    """-> (t, bid, ask) sorted, deduplicated by price change. base 'e': t = rx − lag, order by (e, rx); 'rx': t = rx, drop lag > 2 s and E regressions."""
    rx, lag, bid, ask = r["rx"], r["lag"], r["bid"], r["ask"]
    e = rx - lag
    if base == "e":
        o = np.lexsort((rx, e))
        t, b, a = e[o], bid[o], ask[o]
    else:
        o = np.argsort(rx, kind="stable")
        rx2, e2, b, a, lg = rx[o], e[o], bid[o], ask[o], lag[o]
        cm = np.maximum.accumulate(e2)
        ok = (lg <= 2000) & (e2 >= np.r_[cm[:1], cm[:-1]])
        t, b, a = rx2[ok], b[ok], a[ok]
    ok = (b > 0) & (a > 0) & (b <= a)
    t, b, a = t[ok], b[ok], a[ok]
    keep = [0]
    for i in range(1, len(t)):
        if b[i] != b[keep[-1]] or a[i] != a[keep[-1]]:
            keep.append(i)
    k = np.array(keep)
    return t[k], b[k], a[k]


def rows(A, B):
    """Union of change moments; state of each side = last record with t <= T. -> lists T, S, Bu, src ('A'/'B'/'AB'), ageOther."""
    tA, bA, aA = A
    tB, bB, aB = B
    T = sorted(set(tA.tolist()) | set(tB.tolist()))
    out = []
    sa, sb = set(tA.tolist()), set(tB.tolist())
    for t in T:
        i = bisect_right(tA, t) - 1
        j = bisect_right(tB, t) - 1
        if i < 0 or j < 0:
            continue
        S = (bA[i] / aB[j] - 1) * 1e4
        Bu = (aA[i] / bB[j] - 1) * 1e4
        src = ("A" if t in sa else "") + ("B" if t in sb else "")
        age = (t - tB[j]) if src == "A" else (t - tA[i]) if src == "B" else 0
        out.append((t, S, Bu, src, age))
    return out


def centre_of(R):
    close = {}
    for t, S, Bu, _, _ in R:
        close[t // 60000] = (S + Bu) / 2
    cen = {}
    ks = sorted(close)
    for k in range(ks[0], ks[-1] + 2):
        w = [close[m] for m in range(k - 60, k) if m in close]
        cen[k] = median(w) if len(w) >= 30 else None
    return cen


def episodes(R, cen):
    eps, st = [], {1: None, -1: None}
    for idx, (t, S, Bu, src, age) in enumerate(R):
        c = cen.get(t // 60000)
        if c is None:
            continue
        M = (S + Bu) / 2
        for d in (1, -1):
            e = S - c if d == 1 else c - Bu
            o = st[d]
            if o is None:
                if e >= TH:
                    st[d] = [t, S, Bu, e, idx, src, age]
            else:
                o[3] = max(o[3], e)
                if (d == 1 and M <= c) or (d == -1 and M >= c):
                    gross = o[1] - Bu if d == 1 else S - o[2]
                    eps.append((o[0], d, t - o[0], o[3], gross, o[4], idx, o[5], o[6]))
                    st[d] = None
    return eps


def state_at(V, t):
    tt, b, a = V
    i = bisect_right(tt, t) - 1
    return (b[i], a[i]) if i >= 0 else (None, None)


W = pickle.load(open(f"{D}/state.pkl", "rb"))
t_cut = W.t_a - 10 * 60000
raw = {ex: load(ex) for ex in ("binance", "bybit", "bingx")}
for ex, r in raw.items():
    e = r["rx"] - r["lag"]
    g = np.diff(np.sort(e))
    print(f"{ex}: records {len(e)}, span {e.min()}..{e.max()}, E-gap p50 {np.median(g):.0f} ms p90 {np.percentile(g, 90):.0f} ms; lag p50 {np.median(r['lag']):.0f} p99 {np.percentile(r['lag'], 99):.0f} ms", flush=True)
for ea, eb in (("binance", "bybit"), ("binance", "bingx"), ("bybit", "bingx")):
    print(f"\n=== EVAA {ea}-{eb}", flush=True)
    res = {}
    for base in ("e", "rx"):
        A, B = view(raw[ea], base), view(raw[eb], base)
        t_lo = max(A[0][0], B[0][0]) + 90 * 60000
        R = rows(A, B)
        cen = centre_of(R)
        eps = [x for x in episodes(R, cen) if t_lo <= x[0] < t_cut]
        res[base] = (eps, A, B, R)
        tau = np.array([x[2] for x in eps])
        net = np.array([x[4] for x in eps]) - FEE
        print(f"[{base}] eps {len(eps)}  tau p50 {np.median(tau):.0f} ms  share<1s {100 * (tau < 1000).mean():.1f}%  net p50 {np.median(net):.2f} p95 {np.percentile(net, 95):.2f} mean {net.mean():.2f}  share>0 {100 * (net > 0).mean():.1f}%", flush=True)
    eps, A, B, R = res["e"]
    t_lo = eps[0][0] if eps else 0
    ps = W.pairs.get(("EVAA", ea, eb))
    dm = [x for x in ps.closed if t_lo <= x[0] < t_cut]
    dd = {}
    for x in dm:   # raw rx here is rounded, daemon floors: match starts within ±2 ms
        for s_ in (-2, -1, 0, 1, 2):
            dd.setdefault((x[0] + s_, x[1]), x)
    hit = [x for x in eps if (x[0], x[1]) in dd]
    print(f"daemon closed eps same span {len(dm)}; mine {len(eps)}; matched within 2 ms {len(hit)} ({100 * len(hit) / max(len(eps), 1):.2f} %)")
    dif = [abs(x[4] - dd[(x[0], x[1])][5]) for x in hit]
    dt = [abs(x[2] - dd[(x[0], x[1])][2]) for x in hit]
    if dif:
        print(f"matched: net diff p50 {np.median(dif):.4f} p99 {np.percentile(dif, 99):.3f} bps; tau diff p50 {np.median(dt):.0f} p99 {np.percentile(dt, 99):.0f} ms")
    # who triggers the entry, and how old is the other side's quote then
    src = {}
    for x in eps:
        src.setdefault(x[7], []).append(x[8])
    for s, ages in src.items():
        a = np.array(ages)
        print(f"entry triggered by {'+'.join({'A': ea, 'B': eb}[c] for c in s)}: {len(a)} eps; other side quote age p50 {np.median(a):.0f} ms p90 {np.percentile(a, 90):.0f} ms")
    # who closes it
    xs = {}
    for x in eps:
        xs[R[x[6]][3]] = xs.get(R[x[6]][3], 0) + 1
    print("exit row source:", {('+'.join({'A': ea, 'B': eb}[c] for c in k)): v for k, v in xs.items()})
    # execution delay: enter at T_i + L, exit at T_j + L with prevailing prices (E base)
    for L in (0, 50, 100, 250, 500):
        g = []
        for (ti, d, tau, pk, gross, i, j, s_, ag) in eps:
            bA0, aA0 = state_at(A, ti + L)
            bB0, aB0 = state_at(B, ti + L)
            bA1, aA1 = state_at(A, ti + tau + L)
            bB1, aB1 = state_at(B, ti + tau + L)
            S0, B0 = (bA0 / aB0 - 1) * 1e4, (aA0 / bB0 - 1) * 1e4
            S1, B1 = (bA1 / aB1 - 1) * 1e4, (aA1 / bB1 - 1) * 1e4
            g.append((S0 - B1 if d == 1 else S1 - B0) - FEE)
        g = np.array(g)
        print(f"  delay {L:4d} ms: net p50 {np.median(g):7.2f}  p95 {np.percentile(g, 95):7.2f}  mean {g.mean():7.2f}  share>0 {100 * (g > 0).mean():5.1f}%", flush=True)
