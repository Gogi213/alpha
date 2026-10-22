"""Сводка клеток псевдотеста (pseudotest-cells.csv) в таблицу markdown: гипотеза × рынок, S взвешено по n по месяцам.

Запуск: python tools/compute/pseudotest-summary.py <pseudotest-cells.csv> > таблица.md
"""
import collections
import csv
import math
import sys

R = list(csv.DictReader(open(sys.argv[1], encoding="utf-8")))
g = collections.defaultdict(list)
for r in R:
    g[(r["hyp"], r["src"])].append(r)


def w(rs, c):
    s = t = 0
    for r in rs:
        v = float(r[c])
        n = int(r["n"])
        if not math.isnan(v):
            s += v * n
            t += n
    return s / t if t else float("nan")


def fm(x):
    return ("%.2f" % x).replace(".", ",") if x == x else "—"


print("| гипотеза | рынок | мес | n | S 10 с | S 60 с | S 10 мин | S 1 ч | вверх/вниз 10 мин | мед 10 мин | мес lo>0 | мес hi<0 | топ-3 монет, доля n |")
print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
for (h, s), rs in sorted(g.items()):
    n = sum(int(r["n"]) for r in rs)
    lo = sum(1 for r in rs if float(r["lo_m_10m"]) > 0)
    hi = sum(1 for r in rs if float(r["hi_m_10m"]) < 0)
    print("| %s | %s | %d | %s | %s | %s | %s | %s | %s / %s | %s | %d | %d | %s |" % (
        h, s, len(rs), format(n, ",").replace(",", " "), fm(w(rs, "S_m_10s")), fm(w(rs, "S_m_60s")), fm(w(rs, "S_m_10m")),
        fm(w(rs, "S_m_1h")), fm(w(rs, "up_m_10m")), fm(w(rs, "dn_m_10m")), fm(w(rs, "med_m_10m")), lo, hi, fm(w(rs, "top3_n_share"))))
