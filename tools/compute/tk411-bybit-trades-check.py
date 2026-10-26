#!/usr/bin/env python3
"""TK-411: сверка сборщика свечей по сделкам Bybit (tk411-bybit-trades.py) с API-свечами того же символа за сентябрь.
Использование: tk411-bybit-trades-check.py <api.csv.gz> <собранное.csv.gz>; печатает минут в API/в сборке/общих, минут только в одном,
долю совпадения o,h,l,c (строка) и v (отн. разница), quote_v (отн. разница): медиана и max по общим минутам.
"""
import gzip
import sys


def load(p):
    d = {}
    with gzip.open(p, "rt") as f:
        f.readline()
        for line in f:
            x = line.rstrip("\n").split(",")
            d[int(x[0])] = x
    return d


def rel(a, b):
    a, b = float(a), float(b)
    return abs(a - b) / max(abs(a), abs(b), 1e-300)


def med(v):
    v = sorted(v)
    return v[len(v) // 2] if v else float("nan")


a, b = load(sys.argv[1]), load(sys.argv[2])
com = sorted(set(a) & set(b))
print("API %d, сборка %d, общих %d, только API %d, только сборка %d" % (len(a), len(b), len(com), len(set(a) - set(b)), len(set(b) - set(a))))
for i, nm in ((1, "o"), (2, "h"), (3, "l"), (4, "c")):
    eq = sum(1 for t in com if rel(a[t][i], b[t][i]) < 1e-9)
    print("%s: совпало %d из %d (%.4f)" % (nm, eq, len(com), eq / max(len(com), 1)))
for i, nm in ((5, "v"), (6, "quote_v")):
    r = [rel(a[t][i], b[t][i]) for t in com]
    print("%s: отн.разница медиана %.2e max %.2e, >1e-6: %d" % (nm, med(r), max(r) if r else float("nan"), sum(1 for x in r if x > 1e-6)))
