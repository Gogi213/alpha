#!/usr/bin/env python3
"""Судья, независимый пересчёт П-02 вторая очередь (Г-07, Г-88) из компактных суток.
Своя реализация (не p02-wall2.py). Считает ОБЕ популяции: с TRX и без TRX.
Выход: JSON с порогами (свой расчёт) и суточными счётчиками по бакетам на ЗАМОРОЖЕННЫХ границах."""
import csv, glob, gzip, json, math, os, sys
from array import array
from collections import defaultdict

FROZEN_LO, FROZEN_HI = 4581393.0, 9657216.0  # из протокола (172b51d)
DIRS = {"2026-08": ["epochs/e-aug/study/p02c/aug"],
        "2026-09": ["epochs/e-archive/study/p02c/sept", "study/p02c/sept"]}

def q7(s, q):
    k = (len(s) - 1) * q
    f = math.floor(k); c = math.ceil(k)
    return s[f] if f == c else s[f] * (c - k) + s[c] * (k - f)

frozen = json.load(open("study/p02c/p02-wall2-thresholds.json"))
persym = frozen["per_symbol_terc"]

all_aug = array("d"); notrx_aug = array("d")
days = {}
for month, dirs in DIRS.items():
    for d in dirs:
        for path in sorted(glob.glob(os.path.join(d, "20*.csv.gz"))):
            day = os.path.basename(path)[:10]
            assert day not in days, day
            # ключ: (вариант, популяция, бакет) -> [bounced, n]
            cnt = defaultdict(lambda: [0, 0])
            syms = set()
            with gzip.open(path, "rt", newline="") as f:
                r = csv.reader(f); h = next(r)
                iS, iE, iD, iR, iDay = (h.index(x) for x in ("symbol", "ended_by_death", "depth_behind_lots", "repeat_count", "day_utc"))
                for row in r:
                    sym = row[iS]; dep = float(row[iD]); rep = int(row[iR])
                    assert row[iDay] == day
                    b = 1 if row[iE] == "false" else 0
                    assert row[iE] in ("true", "false")
                    trx = sym == "TRXUSDT"
                    pops = ("all",) if trx else ("all", "notrx")
                    syms.add(sym)
                    if month == "2026-08":
                        all_aug.append(dep)
                        if not trx: notrx_aug.append(dep)
                    gb = "top" if dep >= FROZEN_HI else ("bottom" if dep <= FROZEN_LO else None)
                    st = persym.get(sym)
                    pb = None
                    if st:
                        pb = "top" if dep >= st[1] else ("bottom" if dep <= st[0] else None)
                    hb = "clean" if rep == 0 else "spring"
                    for p in pops:
                        c = cnt[("n", p, "all")]; c[0] += b; c[1] += 1
                        if gb: c = cnt[("g07", p, gb)]; c[0] += b; c[1] += 1
                        if pb: c = cnt[("g07ps", p, pb)]; c[0] += b; c[1] += 1
                        c = cnt[("g88", p, hb)]; c[0] += b; c[1] += 1
            days[day] = {"month": month, "nsym": len(syms), "nopersym": sorted(s for s in syms if s not in persym),
                         "cnt": {"|".join(k): v for k, v in cnt.items()}}
            print(day, len(syms), cnt[("n", "all", "all")][1], file=sys.stderr, flush=True)

out = {"days": days}
for name, arr in (("all", all_aug), ("notrx", notrx_aug)):
    s = sorted(arr)
    out["thr_" + name] = {"n": len(s), "lo": q7(s, 1/3), "hi": q7(s, 2/3), "p10": q7(s, .1), "p50": q7(s, .5), "p90": q7(s, .9)}
    del s
json.dump(out, open("/tmp/judge-p02w2.json", "w"))
print(json.dumps({k: v for k, v in out.items() if k.startswith("thr")}))
