#!/usr/bin/env python3
"""TK-443 V10 (H-025): после события v11 вверх — неудачные возвраты выше уровня → дамп. Уровень mid=(c[t_start]+c[t_peak])/2 (середина хода).
t0 = первая минута после t_peak, где c<mid; неудачный возврат = переход c>=mid снизу с обратным закрытием <mid в течение 60 мин; N считается в [t0;t0+180].
Решение в t1=t0+180 при mid>c[t1]>c[t_start]; исход: дамп = min c(t1;t1+360]<=c[t_start]. Группы N=0|1|2|3+; база = все условные события.
  python3 tk443-v10.py <куб> <events.csv> <вых.csv> [месяц-метка]"""
import sys
import numpy as np

MIN = 60000


def main(cube, ev, out, tag=""):
    sy = open(f"{cube}/bybit-linear.syms").read().split()
    lo = int(open(f"{cube}/bybit-linear.lo").read().split()[0])
    A = np.load(f"{cube}/bybit-linear.npy", mmap_mode="r")
    ix = {s: i for i, s in enumerate(sy)}
    rows = [l.rstrip("\n").split(",") for l in open(ev)][1:]
    rows = [r for r in rows if r[2] == "bybit-linear" and r[4] == "+" and r[3] in ix and int(r[0]) in (360, 720)]
    T = A.shape[2]
    cache = {}
    res = []
    rng = np.random.default_rng(442)
    drop = {"no_t0": 0, "cut": 0, "state": 0}
    for r in rows:
        D, s = int(r[0]), r[3]
        if s not in cache:
            c = np.array(A[ix[s], 3, :], dtype=np.float64)
            idx = np.where(np.isfinite(c), np.arange(T), 0)
            np.maximum.accumulate(idx, out=idx)
            cache[s] = c[idx]
        c = cache[s]
        a, p = int(r[5]) // MIN - lo, int(r[6]) // MIN - lo
        if a < 0 or p >= T or not (c[a] > 0):
            continue
        mid = (c[a] + c[p]) / 2
        seg = c[p + 1:min(p + 1 + 720, T)]
        below = np.nonzero(seg < mid)[0]
        if not len(below):
            drop["no_t0"] += 1
            continue
        t0 = p + 1 + below[0]
        t1 = t0 + 180
        if t1 + 360 >= T:
            drop["cut"] += 1
            continue
        w = c[t0:t1 + 1] >= mid
        N = 0
        for j in np.nonzero(w[1:] & ~w[:-1])[0] + 1:      # переход снизу вверх
            nxt = c[t0 + j:t0 + j + 61] < mid
            if nxt.any():
                N += 1
        if not (c[a] < c[t1] < mid):
            drop["state"] += 1
            continue
        dump = bool((c[t1 + 1:t1 + 361] <= c[a]).any())
        res.append((D, s, (lo + t0) // 1440, N, dump))
    print(len(rows), "событий; отброшено", drop, "в счёте", len(res), flush=True)
    outl = []
    for Dsel in (360, 720, 0):
        R = [x for x in res if Dsel == 0 or x[0] == Dsel]
        if not R:
            continue
        keys = sorted({(x[1], x[2]) for x in R})
        kid = {k: i for i, k in enumerate(keys)}
        cid = np.array([kid[(x[1], x[2])] for x in R])
        Nn = np.array([x[3] for x in R])
        dm = np.array([x[4] for x in R], float)
        NC = len(keys)
        Wt = rng.multinomial(NC, np.full(NC, 1 / NC), size=200).astype(float)
        def stat(sel):
            n_ = np.bincount(cid[sel], minlength=NC).astype(float)
            h_ = np.bincount(cid[sel], weights=dm[sel], minlength=NC)
            return n_, h_
        nb, hb = stat(np.ones(len(R), bool))
        base = dm.mean()
        for nm, sel in (("N=0", Nn == 0), ("N=1", Nn == 1), ("N=2", Nn == 2), ("N>=3", Nn >= 3), ("N>=1", Nn >= 1), ("N>=2", Nn >= 2)):
            if not sel.any():
                continue
            ns, hs = stat(sel)
            with np.errstate(all="ignore"):
                pb = (Wt @ hs) / (Wt @ ns)
                lb = pb / ((Wt @ hb) / (Wt @ nb))
            pr = dm[sel].mean()
            outl.append((tag, Dsel or "все", nm, len(R), int(sel.sum()), round(base, 4), round(pr, 4), round(pr / base, 3),
                         round(np.nanpercentile(lb, 2.5), 3), round(np.nanpercentile(lb, 97.5), 3), NC))
    with open(out, "w") as f:
        f.write("месяц,D,группа,условных_событий,в_группе,база_дамп,доля_дампа,подъём,подъём_lo,подъём_hi,кластеров\n")
        for o in outl:
            f.write(",".join(map(str, o)) + "\n")


if __name__ == "__main__":
    main(*sys.argv[1:5])
