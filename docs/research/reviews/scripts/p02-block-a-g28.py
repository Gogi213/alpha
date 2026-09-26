#!/usr/bin/env python3
"""Судья: блок A, Г-28 — из чего состоит признак и держится ли знак внутри монеты (чтение для проверки толкования)."""
import csv, glob, json, os, sys
from array import array
from collections import defaultdict
LO, HI = 2432.0, 27125.0  # общий терциль из отчёта
BASES = {"2026-08": ["epochs/e-aug/study/approaches/D20"],
         "2026-09": ["epochs/e-archive/study/approaches/D20", "study/approaches/D20"]}
def q(s, p):
    k = (len(s)-1)*p; f = int(k); c = min(f+1, len(s)-1); return s[f] + (s[c]-s[f])*(k-f)
def files(month):
    for b in BASES[month]:
        for d in sorted(glob.glob(os.path.join(b, month + "-*"))):
            day = os.path.basename(d)
            if day > "2026-09-23": continue
            for p in sorted(glob.glob(os.path.join(d, "touches-*.csv"))):
                sym = os.path.basename(p)[8:-4]
                if sym == "TRXUSDT": continue
                yield day, sym, p
def rows(p):
    with open(p, newline="") as f:
        r = csv.reader(f); h = next(r)
        iS, iF, iE = h.index("stack_levels"), h.index("frontrun_lots"), h.index("ended_by_death")
        for x in r:
            yield int(x[iS]), int(x[iF] or 0), x[iE] == "false"
# проход 1: пороги внутри монеты по августу и доля «сумма ≈ frontrun»
per = defaultdict(lambda: array("d"))
fr_dom = tot = 0
for day, sym, p in files("2026-08"):
    for s, fr, b in rows(p):
        v = s + fr; per[sym].append(v); tot += 1
        if fr >= 10 * s: fr_dom += 1
thr = {}
for sym, a in per.items():
    s = sorted(a); thr[sym] = (q(s, 1/3), q(s, 2/3))
del per
# проход 2: суточные счётчики общий терциль / внутри монеты
days = {}
for month in ("2026-08", "2026-09"):
    for day, sym, p in files(month):
        c = days.setdefault(day, {"g": [0, 0, 0, 0], "w": [0, 0, 0, 0]})
        t = thr.get(sym)
        for s, fr, b in rows(p):
            v = s + fr
            if v >= HI: c["g"][0] += b; c["g"][1] += 1
            elif v <= LO: c["g"][2] += b; c["g"][3] += 1
            if t:
                if v >= t[1]: c["w"][0] += b; c["w"][1] += 1
                elif v <= t[0]: c["w"][2] += b; c["w"][3] += 1
json.dump({"fr_dom_share_aug": fr_dom / tot, "n_aug": tot, "days": days}, open("/tmp/judge-g28.json", "w"))
print("fr>=10*stack share (Aug):", fr_dom / tot, "n", tot)
