"""TK-500: сверка <out>.features.csv ядра с features_<θ>.csv эталона TK-501 (по порядку строк внутри θ; t_start в эталоне усечён до 10 цифр).
Запуск: cmp_feats.py <core.features.csv> <features_5.04.csv> <features_12.6.csv>"""
import csv, math, sys

COLS = ["d_in", "depth_slip", "spread", "vol5m", "plevel", "flow1m", "bookimb", "leader", "hour"]
core = {}
for r in csv.DictReader(open(sys.argv[1])):
    core.setdefault(r["theta"], []).append(r)
tot = {c: [0, 0] for c in COLS}
for th, path in (("5.04", sys.argv[2]), ("12.6", sys.argv[3])):
    ref = list(csv.DictReader(open(path)))
    mine = core.get(th, [])
    if len(ref) != len(mine):
        print("число строк", th, "эталон", len(ref), "ядро", len(mine))
    for a, b in zip(ref, mine):
        if abs(float(a["t_start"]) - float(b["t_start"])) > 1e4:
            print("t_start не совпал", th, a["t_start"], b["t_start"])
            continue
        for c in COLS:
            x, y = float(a[c]), float(b[c])
            ok = (math.isnan(x) and math.isnan(y)) or abs(x - y) <= 1e-6 * max(1.0, abs(x))
            tot[c][0] += 1
            tot[c][1] += ok
            if not ok:
                print("расхождение", th, c, a["t_start"], "эталон", x, "ядро", y)
print({c: "%d/%d" % (v[1], v[0]) for c, v in tot.items()})
