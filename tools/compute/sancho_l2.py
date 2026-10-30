#!/usr/bin/env python3
"""TK-451 / P-TK451 §5, §10: слой 2 — реальные события + контроли C1 (псевдо-уровни) и C2 (бары без условия).
python tools/compute/sancho_l2.py <csv.gz> <tf_min> <k> <out.csv.gz>
Строка = событие (role real|C1|C2; ctl_of = индекс реального события, для контролей). Исходы при N=10 от t0 (момент знания):
  a0: t0=t (A1/A5), a1: t0=t+1 (A7: слом), a2: t0=t+2 (A6/A8: имбаланс, оба). Для каждого t0: unres (не было close за L до t0 включительно),
  x1/x2 от t0. imb = l[t] > h[t+2] (медвежий для свипа хая; лой — зеркалом), bos = c[t+1] < последнего подтв. к t свинг-лоя.
"""
import os
import sys
import hashlib
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sancho import bars, atr14, swings  # noqa: E402

N = 10
WIN = 30  # суток
RATIO = 1.25
MAXC = 5


def feats(j, L, h, l, c, ok, atr, slc_conf, sl_idx, n):
    """признаки события в баре j (уровень L, хай-ориентация). dict либо None если вне данных."""
    brk = c[j] > L
    r = dict(brk=int(brk))
    for nm, off in (("a0", 0), ("a1", 1), ("a2", 2)):
        t0 = j + off
        if t0 + N >= n or not ok[j:t0 + N + 1].all():
            r[f"u_{nm}"] = r[f"x1_{nm}"] = r[f"x2_{nm}"] = -1
            continue
        if brk:
            r[f"u_{nm}"] = int((c[j:t0 + 1] > L).all()) if off else 1   # пробой: «не вернулся» до t0
            cl = c[t0 + 1:t0 + N + 1]
            r[f"x1_{nm}"] = int(c[t0 + N] > L)
            r[f"x2_{nm}"] = int((cl > L).all())
        else:
            r[f"u_{nm}"] = int((c[j:t0 + 1] <= L).all())
            cl = c[t0 + 1:t0 + N + 1]
            r[f"x1_{nm}"] = int(c[t0 + N] <= L)
            r[f"x2_{nm}"] = int((cl <= L).all())
    if not brk and j + 2 < n and ok[j:j + 3].all():
        r["imb"] = int(l[j] > h[j + 2])
        m = np.searchsorted(slc_conf, j, side="right")
        r["bos"] = int(c[j + 1] < l[sl_idx[m - 1]]) if m > 0 else -1
    else:
        r["imb"] = r["bos"] = -1
    return r


def sparse(h):
    t = [h]
    s = 1
    while 2 * s <= len(h):
        p = t[-1]
        t.append(np.maximum(p[:-s], p[s:]))
        s *= 2
    return t


def rmax(tab, a, b):
    """max h[a..b] включительно, векторно (a<=b)."""
    ln = b - a + 1
    k = np.floor(np.log2(ln)).astype(int)
    out = np.empty(len(a))
    for q in np.unique(k):
        m = k == q
        out[m] = np.maximum(tab[q][a[m]], tab[q][b[m] - (1 << q) + 1])
    return out


def dir_rows(a, sgn, k, dname):
    ok = a.ok.values
    tms = a.t.values
    if sgn == 1:
        o, h, l, c = (a[x].values.astype(float) for x in "ohlc")
    else:
        o, h, l, c = -a.o.values.astype(float), -a.l.values.astype(float), -a.h.values.astype(float), -a.c.values.astype(float)
    n = len(h)
    atr = atr14(h, l, c)
    atrbps = 1e4 * np.r_[np.nan, atr[:-1]][:] / np.abs(c)   # ATR на t-1 в bps цены закрытия t (как в слое 1)
    sh = swings(h, ok, k)
    sl = swings(-l, ok, k)
    slc = sl + k
    tab = sparse(h)
    swh = np.zeros(n, bool)
    swh[sh] = True
    # реальные события
    real = []
    for i in sh:
        L = h[i]
        s = i + k + 1
        hit = np.nonzero(h[s:min(n, s + 100000)] > L)[0]
        if not len(hit):
            continue
        j = s + hit[0]
        if not (ok[j] and ok[j - 1]) or np.isnan(atr[j - 1]):
            continue
        real.append((i, j))
    realj = np.array([j for _, j in real], dtype=np.int64)
    realset = np.zeros(n, bool)
    realset[realj] = True
    # C2: псевдо-уровень = max h предыдущих k+1 баров
    pl = pd.Series(h).shift(1).rolling(k + 1).max().values
    c2ok = ok & np.r_[False, ok[:-1]] & ~np.isnan(pl) & ~np.isnan(atrbps) & (h > pl) & ~realset
    c2cls = (c > pl)  # пробой
    hour = ((tms // 3600000) % 24) if True else None
    per = int(np.median(np.diff(tms[ok]))) if ok.sum() > 2 else 3600000
    wb = int(WIN * 86400000 // max(per, 1))   # окно в барах
    hr = (tms // per) % max(1, 86400000 // per) if per < 86400000 else (tms // 86400000) % 7
    rows = []
    for ev_idx, (i, j) in enumerate(real):
        L = h[i]
        f = feats(j, L, h, l, c, ok, atr, slc, sl, n)
        if f["u_a0"] == -1 and f["u_a1"] == -1 and f["u_a2"] == -1:
            # исходы нет, но ядро (брк/глубина) оставим для воронки
            pass
        A = atr[j - 1]
        brk = bool(f["brk"])
        base = dict(dir=dname, t=int(tms[j]), age=int(j - i), atr_bps=atrbps[j], depth=(c[j] - L) / A if brk else np.nan,
                    wick=(h[j] - L) / A)
        rows.append(dict(role="real", ev=ev_idx, **base, **f))
        lo, hi = max(0, j - wb), min(n, j + wb + 1)
        rng = np.random.default_rng(int(hashlib.md5(f"{dname}{tms[j]}".encode()).hexdigest()[:8], 16) ^ 451)
        age = j - i
        # C1: псевдо-уровень i' = j' - age, не свинг, первое касание — j'
        jc = np.arange(lo, hi)
        ic = jc - age
        m = (ic >= 0) & (jc != j)
        jc, ic = jc[m], ic[m]
        m = ok[jc] & ok[jc - 1] & ok[ic] & ~swh[ic] & ~np.isnan(atrbps[jc]) & (h[jc] > h[ic])
        jc, ic = jc[m], ic[m]
        m = (atrbps[jc] <= atrbps[j] * RATIO) & (atrbps[jc] * RATIO >= atrbps[j])
        jc, ic = jc[m], ic[m]
        if len(jc):
            a_ = ic + k + 1
            b_ = jc - 1
            has = b_ >= a_
            mx = np.full(len(jc), -np.inf)
            if has.any():
                mx[has] = rmax(tab, a_[has], b_[has])
            m = mx < h[ic]
            jc, ic = jc[m], ic[m]
        # класс совпадает с реальным
        if len(jc):
            m = (c[jc] > h[ic]) == brk
            jc, ic = jc[m], ic[m]
        if len(jc) > MAXC:
            pick = rng.choice(len(jc), MAXC, replace=False)
            jc, ic = jc[pick], ic[pick]
        for jj, ii in zip(jc, ic):
            g = feats(jj, h[ii], h, l, c, ok, atr, slc, sl, n)
            rows.append(dict(role="C1", ev=ev_idx, dir=dname, t=int(tms[jj]), age=int(age), atr_bps=atrbps[jj],
                             depth=(c[jj] - h[ii]) / atr[jj - 1] if g["brk"] else np.nan, wick=(h[jj] - h[ii]) / atr[jj - 1], **g))
        # C2: тот же час, класс совпадает, ATR в окне, не реальное касание
        jc = np.arange(lo, hi)
        m = c2ok[jc] & (hr[jc] == hr[j]) & (jc != j) & (c2cls[jc] == brk)
        m &= (atrbps[jc] <= atrbps[j] * RATIO) & (atrbps[jc] * RATIO >= atrbps[j])
        jc = jc[m]
        if len(jc) > MAXC:
            jc = rng.choice(jc, MAXC, replace=False)
        for jj in jc:
            g = feats(jj, pl[jj], h, l, c, ok, atr, slc, sl, n)
            rows.append(dict(role="C2", ev=ev_idx, dir=dname, t=int(tms[jj]), age=-1, atr_bps=atrbps[jj],
                             depth=(c[jj] - pl[jj]) / atr[jj - 1] if g["brk"] else np.nan, wick=(h[jj] - pl[jj]) / atr[jj - 1], **g))
    return rows


def l2_table(path, tf, k):
    a = bars(path, tf)
    rows = dir_rows(a, 1, k, "H") + dir_rows(a, -1, k, "L")
    return pd.DataFrame(rows)


if __name__ == "__main__":
    path, tf, k, out = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
    d = l2_table(path, tf, k)
    d.insert(0, "sym", os.path.basename(path).split(".")[0])
    d.to_csv(out, index=False, compression="gzip")
    print(out, len(d), d.role.value_counts().to_dict())
