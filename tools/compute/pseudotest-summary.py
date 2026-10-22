#!/usr/bin/env python3
"""Сводка клеток псевдотеста: взвешенно по n по месяцам; опционально канарейка против другого CSV.

  pseudotest-summary.py <cells.csv> [--canary <cells-part1.csv> <hyp>] [--ids ID,ID]
"""
import argparse
import csv
import math
import sys


def rd(p):
    with open(p, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def fl(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cells")
    ap.add_argument("--canary", nargs=2, metavar=("OTHER", "HYP"))
    a = ap.parse_args()
    rows = rd(a.cells)
    if a.canary:
        other, hyp = a.canary
        o = {(r["hyp"], r["pop"], r["src"], r["month"]): r for r in rd(other)}
        bad = tot = 0
        for r in rows:
            if r["hyp"] != hyp:
                continue
            k = (r["hyp"], r["pop"], r["src"], r["month"])
            q = o.get(k)
            tot += 1
            if q is None:
                bad += 1
                continue
            for c in q:
                if c not in r:
                    continue
                x, y = r[c], q[c]
                if x != y and not (fl(x) == fl(y) or (math.isnan(fl(x)) and math.isnan(fl(y)))):
                    bad += 1
                    print("DIFF", k, c, x, y)
                    break
        print(f"canary {hyp}: клеток {tot}, расхождений {bad}")
        return
    groups = {}
    for r in rows:
        groups.setdefault((r["hyp"], r["src"]), []).append(r)
    print("| гипотеза | рынок | мес | n | S 10 с | S 60 с | S 10 мин | S 1 ч | вверх/вниз 10 мин | мед 10 мин | мес lo>0 | мес hi<0 | топ-3 монет, доля n | n_calib |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for (h, s), g in sorted(groups.items()):
        n = sum(int(r["n"]) for r in g)

        def w(c):
            num = den = 0.0
            for r in g:
                v = fl(r[c])
                if not math.isnan(v):
                    num += v * int(r["n"])
                    den += int(r["n"])
            return num / den if den else float("nan")

        lo = sum(1 for r in g if fl(r["lo_m_10m"]) > 0)
        hi = sum(1 for r in g if fl(r["hi_m_10m"]) < 0)
        nc = sum(int(r.get("n_calib") or 0) for r in g)
        f = lambda v: "н/д" if math.isnan(v) else f"{v:.2f}".replace(".", ",")
        print(
            f"| {h} | {s} | {len(g)} | {n:,} | {f(w('S_m_10s'))} | {f(w('S_m_60s'))} | {f(w('S_m_10m'))} | {f(w('S_m_1h'))} "
            f"| {f(w('up_m_10m'))} / {f(w('dn_m_10m'))} | {f(w('med_m_10m'))} | {lo} | {hi} | {f(w('top3_n_share'))} | {nc:,} |"
        )


if __name__ == "__main__":
    sys.exit(main())
