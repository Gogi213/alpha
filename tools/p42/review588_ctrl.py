"""TK-588 review: independent label recompute + random and approach-matched controls, strict and 0.1 %-deep return.

Own code (does not import sigfeat_*). Usage: python3 review588_ctrl.py <episodes.parquet> <cache> <aggtrades_root> <out.parquet> <nproc>
"""
import datetime as dt
import io
import sys
import zipfile
from multiprocessing import Pool

import numpy as np
import pandas as pd

EP, CACHE, AGG, OUT, NPROC = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5])
N_MIN, MIN, DAYMS = 106560, 60000, 86400000
END_DAY = 74  # 14.11 = day 74 -> never read
NCTRL = 20
# mv5 quintile boundaries (half A, from the report) for approach matching
QB = {"long": [0.5067, 1.275, 2.09, 3.168], "short": [0.541, 1.289, 2.049, 3.621]}


def ticks_day(sym, d):
    if d < 0 or d >= END_DAY:
        return None
    name = f"{sym}-aggTrades-{(dt.date(2026, 9, 1) + dt.timedelta(days=d)).isoformat()}"
    try:
        with zipfile.ZipFile(f"{AGG}/{sym}/{name}.zip") as z, z.open(name + ".csv") as fh:
            raw = fh.read()
    except FileNotFoundError:
        return None
    df = pd.read_csv(io.BytesIO(raw), usecols=["price", "transact_time"])
    t = df.transact_time.to_numpy(np.int64) - 1788220800000  # 2026-09-01 00:00 UTC
    p = np.rint(df.price.to_numpy() * 1e8).astype(np.int64)
    o = np.argsort(t, kind="stable")
    return t[o], p[o]


def outcome(P, R, X, f):
    """P: prices in long frame after the start; R: level; breakout P >= R + X before any P < R - f after first P >= R."""
    ge = np.nonzero(P >= R)[0]
    if len(ge) == 0:
        return 1
    tc = ge[0]
    bx = np.nonzero(P >= R + X)[0]
    tx = bx[0] if len(bx) else 1 << 62
    rb = np.nonzero(P[tc + 1:] < R - f)[0]
    tr = tc + 1 + rb[0] if len(rb) else 1 << 62
    if tx < tr:
        return 3
    if tr < tx:
        return 4
    return 2


def lab(P, R):
    aR = abs(R)
    X = -(-aR // 100)
    f = aR // 1000
    return outcome(P, R, X, 0), outcome(P, R, X, f)


def work(args):
    sym, g = args
    a = np.fromfile(f"{CACHE}/{sym}.b42", dtype="<i8", offset=8, count=5 * N_MIN).reshape(5, N_MIN)
    h, l, c, qv = a[1].astype(np.float64), a[2].astype(np.float64), a[3].astype(np.float64), a[4]
    cq = np.concatenate([[0], np.cumsum(qv)])
    pc = np.concatenate([[np.nan], c[:-1]])
    tr = np.fmax(h - l, np.fmax(np.abs(h - pc), np.abs(l - pc)))
    ctr = np.concatenate([[0], np.cumsum(np.nan_to_num(tr))])
    natr_m = np.full(N_MIN, np.nan)
    natr_m[29:] = (ctr[30:] - ctr[:-30]) / 30 / np.where(c[29:] > 0, c[29:], np.nan)
    days = {}

    def span(t0, t1):
        ts, ps = [], []
        for d in range(t0 // DAYMS, t1 // DAYMS + 1):
            if d not in days:
                days[d] = ticks_day(sym, d)
            v = days[d]
            if v is None:
                return None
            t, p = v
            i0, i1 = np.searchsorted(t, t0, "left"), np.searchsorted(t, t1, "right")
            ts.append(t[i0:i1]); ps.append(p[i0:i1])
        return np.concatenate(ts), np.concatenate(ps)

    rows = []
    for r in g.itertuples():
        s = 1 if r.side == "long" else -1
        rng = np.random.default_rng([5881, int(r.sig_ms // 1000), int(r.level)])
        sp = span(r.sig_ms, r.sig_ms + 60 * MIN)
        out = {"side": r.side, "sym": sym, "sig_ms": r.sig_ms, "level": r.level}
        if sp is None:
            rows.append(out); continue
        t, p = sp
        P = s * p[t > r.sig_ms]  # strictly after sig_ms
        R = s * int(r.lp_e8)
        out["my_o3"], out["my_o3_f01"] = lab(P, R)
        natr = r.natr_bp / 1e4
        # controls
        d = int(r.day)
        tsig = int(r.sig_ms // MIN)
        mm = np.arange(d * 1440 + 6, (d + 1) * 1440)
        mm = mm[(np.abs(mm - tsig) > 60) & (mm + 61 <= END_DAY * 1440)]
        t1h = (cq[mm] - cq[mm - 60]) / 1e5  # qv is $ x 1e5
        mm = mm[(t1h >= 5e5) & (natr_m[mm - 1] >= 0.004) & (a[0][mm] != 0)]
        mv5c = s * (c[mm - 1] - c[mm - 6]) / c[mm - 1] / natr
        qsig = np.searchsorted(QB[r.side], r.mv5) if r.mv5 == r.mv5 else -1
        qc = np.searchsorted(QB[r.side], mv5c)
        res = {}
        for kind, cand in (("rnd", mm), ("mat", mm[qc == qsig])):
            if len(cand) == 0:
                continue
            pick = rng.choice(cand, size=min(NCTRL, len(cand)), replace=False)
            acc = []
            for m0 in pick:
                sp2 = span(int(m0) * MIN, int(m0) * MIN + 61 * MIN)
                if sp2 is None:
                    continue
                tt, pp = sp2
                if len(tt) == 0 or tt[0] >= int(m0) * MIN + MIN:
                    continue
                PP = s * pp
                P0 = int(PP[0])
                Rc = P0 * 10**8 // (10**8 - s * int(r.dist_e8))
                end = np.searchsorted(tt, tt[0] + 60 * MIN, "right")
                acc.append(lab(PP[1:end], Rc))
            if acc:
                acc = np.array(acc)
                res[f"{kind}_n"] = len(acc)
                res[f"{kind}_o3"] = float((acc[:, 0] == 3).mean())
                res[f"{kind}_o3_f01"] = float((acc[:, 1] == 3).mean())
                res[f"{kind}_touch"] = float((acc[:, 0] != 1).mean())
        out.update(res)
        rows.append(out)
    return rows


if __name__ == "__main__":
    e = pd.read_parquet(EP, filters=[("univ", "==", "U1")])
    e = e[e.ok_t60]
    groups = list(e.groupby("sym"))
    with Pool(NPROC) as pool:
        res = pool.map(work, groups, chunksize=1)
    pd.DataFrame([x for r in res for x in r]).to_parquet(OUT)
    print("ok", sum(len(r) for r in res))
