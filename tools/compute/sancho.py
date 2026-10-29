#!/usr/bin/env python3
"""TK-451 / P-TK451: касания свинг-уровней, классы пробой/свип, якорные A1–A9. Пилот: одна монета, один ТФ, якорь протокола.

python tools/compute/sancho.py <csv.gz> <tf_min> <k> [N=10]
Лой-уровни считаются зеркалом: цены с обратным знаком (h'=-l, l'=-h, o'=-o, c'=-c), дальше код хая.
"""
import sys
import numpy as np
import pandas as pd


def bars(path, tf):
    d = pd.read_csv(path, usecols=[0, 1, 2, 3, 4])
    d.columns = ["t", "o", "h", "l", "c"]
    g = d.t // (tf * 60000)
    a = d.groupby(g, sort=True).agg(t=("t", "first"), o=("o", "first"), h=("h", "max"), l=("l", "min"),
                                    c=("c", "last"), n=("t", "size"))
    a["ok"] = a.n == tf
    # бары с дырой по номеру (нет ни одной минуты) тоже не создают уровней: вставляем пустые
    full = pd.RangeIndex(a.index.min(), a.index.max() + 1)
    a = a.reindex(full)
    a["ok"] = a.ok.fillna(False).astype(bool)
    return a


def atr14(h, l, c):
    pc = np.r_[np.nan, c[:-1]]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    out = np.full(len(h), np.nan)
    cs = np.cumsum(np.nan_to_num(tr))
    bad = np.cumsum(np.isnan(tr))
    for i in range(14, len(h)):
        if bad[i] - bad[i - 14] == 0:
            out[i] = (cs[i] - cs[i - 14]) / 14
    return out


def swings(h, ok, k):
    """swing-high по k свечей с каждой стороны (строго); все 2k+1 бара полные."""
    n = len(h)
    idx = np.arange(k, n - k)
    m = np.ones(len(idx), bool)
    for s in range(1, k + 1):
        m &= h[idx] > h[idx - s]
        m &= h[idx] > h[idx + s]
    for s in range(-k, k + 1):
        m &= ok[idx + s]
    return idx[m]


def events(o, h, l, c, ok, k, N, cap=100000):
    """события для хая-уровней (вход уже зеркалирован). Возвращает dict массивов по касаниям."""
    n = len(h)
    atr = atr14(h, l, c)
    sh = swings(h, ok, k)
    sl = swings(-l, ok, k)  # swing-low
    rows = []
    for i in sh:
        L = h[i]
        s = i + k + 1
        e = min(n, s + cap)
        hit = np.nonzero(h[s:e] > L)[0]
        if not len(hit):
            continue
        j = s + hit[0]
        if not ok[j] or np.isnan(atr[j - 1]) or not ok[j - 1]:
            continue
        rows.append((i, j, L))
    out = []
    for i, j, L in rows:
        a = atr[j - 1]
        brk = c[j] > L
        e = dict(i=i, j=j, L=L, atr=a, brk=brk, depth=(c[j] - L) / a if brk else np.nan,
                 wick=(h[j] - L) / a)
        # якорь автора: исход от касания j, N баров своего ТФ
        if j + N < n and ok[j + 1:j + N + 1].all():
            cl = c[j + 1:j + N + 1]
            e["x1"] = (c[j + N] > L) if brk else (c[j + N] <= L)
            e["x2"] = (cl > L).all() if brk else (cl <= L).all()
        else:
            e["x1"] = e["x2"] = None
        out.append(e)
    return out, atr, sl


def cond_flags(ev, o, h, l, c, ok, atr, sl, k, N):
    """A6/A7/A8 якорь: имбаланс i=t, K=3 (медвежий для свипа хая); слом: close_{t+1} < последнего подтв. swing-low (k) на t.
    Слой 2: t0 = t+2, исход от t0 (X2: ни одного close > L в (t0, t0+N]); база — нерешённые к t0 без условия."""
    n = len(h)
    slc = np.array(sl) + k  # момент подтверждения swing-low
    res = []
    for e in ev:
        if e["brk"]:
            continue
        t = e["j"]
        if t + 2 + N >= n or not ok[t:t + 3 + N].all():
            continue
        L = e["L"]
        imb = l[t] > h[t + 2]
        m = slc <= t
        if not m.any():
            continue
        last = l[sl[np.nonzero(m)[0][-1]]]
        bos = c[t + 1] < last
        unres = (c[t + 1:t + 3] <= L).all()
        cl = c[t + 3:t + 3 + N]
        res.append(dict(t=t, imb=imb, bos=bos, unres=unres, x2_t0=bool((cl <= L).all()),
                        x2_t=bool((c[t + 1:t + 1 + N] <= L).all())))
    return res


def run(path, tf, k, N):
    a = bars(path, tf)
    ok = a.ok.values
    res = {}
    for name, sgn in (("high", 1), ("low", -1)):
        if sgn == 1:
            o, h, l, c = (a[x].values.astype(float) for x in "ohlc")
        else:
            o, h, l, c = -a.o.values.astype(float), -a.l.values.astype(float), -a.h.values.astype(float), -a.c.values.astype(float)
        ev, atr, sl = events(o, h, l, c, ok, k, N)
        cf = cond_flags(ev, o, h, l, c, ok, atr, sl, k, N)
        res[name] = (ev, cf)
    return a, res


def pct(x):
    x = [v for v in x if v is not None]
    return (100 * np.mean(x), len(x)) if x else (float("nan"), 0)


def report(sym, tf, k, N, res):
    ev = res["high"][0] + res["low"][0]
    cf = res["high"][1] + res["low"][1]
    br = [e for e in ev if e["brk"]]
    sw = [e for e in ev if not e["brk"]]
    print(f"== {sym} tf={tf}m k={k} N={N}: касаний {len(ev)}, пробой {len(br)} ({100*len(br)/len(ev):.1f}%), свип {len(sw)}")
    print(f"A1 пробой X1 {pct([e['x1'] for e in br])}  X2 {pct([e['x2'] for e in br])}")
    for lo, hi, nm in ((0, .2, "A2 <0.2"), (.2, .5, "A3 0.2-0.5"), (.5, 9e9, "A4 >0.5")):
        s = [e for e in br if lo <= e["depth"] < hi]
        print(f"{nm}: доля {100*len(s)/max(1,len(br)):.1f}%  X1 {pct([e['x1'] for e in s])} X2 {pct([e['x2'] for e in s])}")
    print(f"A5 свип X1 {pct([e['x1'] for e in sw])}  X2 {pct([e['x2'] for e in sw])}")
    print(f"A9 медиана прокола {np.median([e['wick'] for e in sw]):.3f} ATR")
    allc = cf
    for nm, f in (("A6 имб", lambda r: r["imb"]), ("A7 слом", lambda r: r["bos"]),
                  ("A8 оба", lambda r: r["imb"] and r["bos"])):
        s = [r for r in allc if f(r)]
        b = [r for r in allc if r["unres"] and not f(r)]
        s_u = [r for r in s if r["unres"]]
        print(f"{nm}: доля {100*len(s)/max(1,len(allc)):.1f}% n={len(s)}  слой1 X2 от t {pct([r['x2_t'] for r in s])} | "
              f"слой2 от t0 {pct([r['x2_t0'] for r in s_u])} vs база {pct([r['x2_t0'] for r in b])}")


if __name__ == "__main__":
    path, tf, k = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    N = int(sys.argv[4]) if len(sys.argv) > 4 else 10
    import os
    import time
    t0 = time.time()
    a, res = run(path, tf, k, N)
    print(f"бары {len(a)}, неполных {int((~a.ok).sum())}, {time.time()-t0:.1f} с")
    report(os.path.basename(path).split(".")[0], tf, k, N, res)
