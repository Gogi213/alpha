import numpy as np, pandas as pd, numba as nb

@nb.njit(cache=True)
def kern(code, idx, s, nl):
    # code: 0 set,1 make,2 take; idx: уровень цены; L[level] подписанный размер
    L = np.zeros(nl); n = len(code)
    flips = 0; crossed = 0; chk = 0
    bb = -1; ba = nl
    lb = -2; la = -2; lastj = -1
    ages = np.empty(n, np.float64); na = 0
    chg = np.zeros(n, np.bool_)
    for j in range(n):
        i = idx[j]
        if code[j] == 0:
            L[i] = s[j]
            if s[j] > 0 and i > bb: bb = i
            elif s[j] < 0 and i < ba: ba = i
            continue
        old = L[i]
        new = old + s[j] if code[j] == 1 else old - s[j]
        if old != 0 and new != 0 and (old > 0) != (new > 0): flips += 1
        if abs(new) < 1e-12: new = 0.0
        L[i] = new
        if new > 0 and i > bb: bb = i
        if new < 0 and i < ba: ba = i
        while bb >= 0 and L[bb] <= 0: bb -= 1
        while ba < nl and L[ba] >= 0: ba += 1
        if bb >= 0 and ba < nl:
            chk += 1
            if bb >= ba: crossed += 1
            if bb != lb or ba != la:
                chg[j] = True; lb = bb; la = ba
    return flips, crossed, chk, chg

def hour(path):
    df = pd.read_csv(path, header=None, names=["t", "e", "p", "s", "i", "f"], usecols=[0, 1, 2, 3], dtype={"t": "float64", "e": "category", "p": "float64", "s": "float64"})
    ev = df["e"].astype(str).map({"set": 0, "make": 1, "take": 2}).to_numpy(np.int8)
    t = df["t"].to_numpy(); p = df["p"].to_numpy(); s = df["s"].to_numpy()
    up, inv = np.unique(p, return_inverse=True)
    flips, crossed, chk, chg = kern(ev, inv.astype(np.int64), s, len(up))
    q = lambda a, qs: [float(x) for x in np.percentile(a, qs)] if len(a) else None
    d = np.diff(t); tc = t[chg]
    return {"rows": len(t), "ev": {"set": int((ev == 0).sum()), "make": int((ev == 1).sum()), "take": int((ev == 2).sum())},
            "ts_nonmono": int((d < 0).sum()), "gap_s_q": q(d, [50, 99, 99.9]), "gap_max_s": float(d.max()) if len(d) else None,
            "flips": int(flips), "crossed": int(crossed), "checked": int(chk), "best_age_s_q": q(np.diff(tc), [50, 99]),
            "ts_first": float(t[0]) if len(t) else None, "ts_last": float(t[-1]) if len(t) else None}
