#!/usr/bin/env python3
"""TK-455 / P-TK455 §1-2: сигналы слоя 3 — реальные события A1, A4, A5, A6, A7, A8 тем же кодом, что слой 2 (sancho_l2.feats).
python tools/compute/sancho_l3_sig.py <csv.gz> <tf_min> <k> <out.csv.gz>
Строка = сигнал: dir H/L (сторона события; ожидаемая сторона сделки: A5-A8 — против свипа (H -> шорт), A1/A4 — по пробою),
j = бар касания, t0 = бар момента знания (A1/A4/A5: j; A7: j+1; A6/A8: j+2), L = уровень, ext = экстремум свипа (хай бара j),
atr_bps = ATR14 на j-1 в bps, depth = глубина закрытия в ATR (пробой). Всё — по данным <= t0. Исходы здесь не считаются."""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sancho import bars, atr14, swings  # noqa: E402
from sancho_l2 import feats, N  # noqa: E402


def sig_dir(a, sgn, k, dname):
    ok = a.ok.values
    tms = a.t.values
    if sgn == 1:
        o, h, l, c = (a[x].values.astype(float) for x in "ohlc")
    else:
        o, h, l, c = -a.o.values.astype(float), -a.l.values.astype(float), -a.h.values.astype(float), -a.c.values.astype(float)
    n = len(h)
    atr = atr14(h, l, c)
    atrbps = 1e4 * np.r_[np.nan, atr[:-1]] / np.abs(c)
    sh = swings(h, ok, k)
    sl = swings(-l, ok, k)
    slc = sl + k
    rows = []
    for i in sh:
        L = h[i]
        s = i + k + 1
        hit = np.nonzero(h[s:min(n, s + 100000)] > L)[0]
        if not len(hit):
            continue
        j = s + hit[0]
        if not (ok[j] and ok[j - 1]) or np.isnan(atr[j - 1]):
            continue
        f = feats(j, L, h, l, c, ok, atr, slc, sl, n)
        brk = bool(f["brk"])
        depth = (c[j] - L) / atr[j - 1] if brk else np.nan
        base = dict(dir=dname, i=int(i), j=int(j), L=sgn * L, ext=sgn * h[j], atr_bps=atrbps[j], depth=depth, tj=int(tms[j]))

        def add(ev, t0, flag=1):
            # t0 должен иметь N баров после и сплошные данные — как в слое 2 (иначе исход слоя 2 = -1, сигнал в воронку не идёт)
            if t0 + N < n and ok[j:t0 + N + 1].all():
                rows.append(dict(ev=ev, t0=int(t0), tt0=int(tms[t0]), **base))

        if brk:
            add("A1", j)
            if depth > 0.5:
                add("A4", j)
        else:
            add("A5", j)
            if f["imb"] == 1:
                add("A6", j + 2)
            if f["bos"] == 1:
                add("A7", j + 1)
            if f["imb"] == 1 and f["bos"] == 1:
                add("A8", j + 2)
    return rows


def sig_table(path, tf, k):
    a = bars(path, tf)
    return pd.DataFrame(sig_dir(a, 1, k, "H") + sig_dir(a, -1, k, "L"))


if __name__ == "__main__":
    path, tf, k, out = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
    d = sig_table(path, tf, k)
    d.insert(0, "sym", os.path.basename(path).split(".")[0])
    d.to_csv(out, index=False, compression="gzip")
    print(out, len(d), d.ev.value_counts().to_dict() if len(d) else {})
