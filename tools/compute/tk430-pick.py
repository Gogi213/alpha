#!/usr/bin/env python3
"""TK-430: список контрактов минуты T (по умолчанию 30.09 08:00 UTC) со свечой Gate fut: признак артефакта, цена, оборот минуты.
Использование: tk430-pick.py <каталог gate-futures_usdt> [t_open_ms]; выход: инструмент<TAB>артефакт<TAB>close<TAB>quote_v<TAB>h/c<TAB>l/c.
"""
import gzip
import os
import sys

src = sys.argv[1]
t = sys.argv[2] if len(sys.argv) > 2 else "1790755200000"
for fn in sorted(os.listdir(src)):
    if not fn.endswith(".csv.gz"):
        continue
    with gzip.open(os.path.join(src, fn), "rt") as f:
        next(f)
        for line in f:
            if not line.startswith(t + ","):
                continue
            p = line.split(",")
            o, h, l, c, qv = float(p[1]), float(p[2]), float(p[3]), float(p[4]), float(p[6])
            bad = l < 0.5 * min(o, c) or h > 2.0 * max(o, c)
            print("%s\t%d\t%g\t%.1f\t%.3f\t%.4f" % (fn[:-7], bad, c, qv, h / c, l / c))
            break
