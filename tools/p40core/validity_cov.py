# TK-522: покрытие validity.csv.gz планом D1 (нога × сутки 17–30.09, имена бирж как в validity). Запуск на srv: python3 validity_cov.py
import csv, gzip, collections
V = collections.defaultdict(set)
for r in csv.DictReader(gzip.open("/root/p40core/validity.csv.gz", "rt")):
    V[(r["ex"], r["contract"], r["date"])].add(r["hour"])
legs = {}
for r in csv.DictReader(open("/root/p40core/pairs_d1_v3_nobitget.csv")):
    for c in "AB":
        e, s = r["ex" + c], r["sym" + c]; e = {"binance": "binance_futures", "okx": "okx_futures"}.get(e, e)
        if e: legs[(e, s)] = 1
days = ["2026-09-%02d" % d for d in range(17, 31)]
by = collections.defaultdict(lambda: [0, 0, 0, 0])
for (e, s) in legs:
    for d in days:
        h = len(V.get((e, s, d), ()))
        b = by[e]; b[0] += 1; b[1] += (h == 24); b[2] += (0 < h < 24); b[3] += (h == 0)
print("legs", len(legs))
for e, b in sorted(by.items()): print(e, "leg-days", b[0], "full24", b[1], "partial", b[2], "none", b[3])
print("validity exchanges:", sorted({k[0] for k in V}), "date range", min(k[2] for k in V), max(k[2] for k in V))
