#!/usr/bin/env python3
"""TK-455 §7 «Алгоритм»: сверка ускорения (один скан на группу ячеек, меньшие N выводятся из пути при N_max) с прямым счётом
каждой ячейки. python tools/compute/sancho_l3_derive_check.py <csv.gz> <tf> <k> <fund_dir> [<число сигналов>]. Расхождение -> код 1."""
import sys, os, time
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sancho_l3_trades as T
from sancho_l3_sig import sig_table

path, tf, k, fc = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
lim = int(sys.argv[5]) if len(sys.argv) > 5 else 300
cn = T.Coin(path, tf, k, fc)
sg = sig_table(path, tf, k)
sg = sg.iloc[np.linspace(0, len(sg) - 1, min(lim, len(sg))).astype(int)]
bad = n = 0
t_dir = t_der = 0.0
for s in sg.to_dict("records"):
    d = T.side_of(s["ev"], s["dir"])
    groups = {}
    for c in T.cells(s["ev"], False):
        groups.setdefault(c[:7], []).append(c[7])
    for (form, E, W, S, m, Tt, r), ns in groups.items():
        t = time.time()
        direct = {x: T.run_signal(cn, s, d, form, E, W, S, m, Tt, r, x)[0] for x in ns}
        t_dir += time.time() - t
        t = time.time()
        rb, big = T.run_signal(cn, s, d, form, E, W, S, m, Tt, r, max(ns))
        der = {x: (rb if x == max(ns) else (T.derive_n(cn, d, big, x, W) if not isinstance(rb, str) else rb)) for x in ns}
        t_der += time.time() - t
        for x in ns:
            a, b = direct[x], der[x]
            n += 1
            same = (a == b) if isinstance(a, str) or isinstance(b, str) else all(
                (a[q] == b[q]) or (isinstance(a[q], float) and np.isnan(a[q]) and np.isnan(b[q])) for q in a)
            if not same:
                bad += 1
                if bad < 4:
                    print("DIFF", form, E, W, S, m, Tt, r, x, a, b)
print(f"cells {n} bad {bad} direct {t_dir:.1f}s derived {t_der:.1f}s signals {len(sg)}")
sys.exit(1 if bad else 0)
