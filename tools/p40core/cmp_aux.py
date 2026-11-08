"""TK-500: сверка <out>.aux.csv (oi5m, liq5m, phi_bps) с features_<θ>.csv эталона TK-501 по порядку строк внутри θ.
Запуск: cmp_aux.py <core.aux.csv> <features_5.04.csv> <features_12.6.csv>"""
import csv, math, sys

COLS = [("oi5m", 1e-9), ("liq5m", 0), ("phi_bps", 1e-9), ("n_s", 0)]
core = {}
for r in csv.DictReader(open(sys.argv[1])):
    core.setdefault(r["theta"], []).append(r)
tot = {c: [0, 0] for c, _ in COLS}
for th, path in (("5.04", sys.argv[2]), ("12.6", sys.argv[3])):
    ref = list(csv.DictReader(open(path)))
    mine = core.get(th, [])
    if len(ref) != len(mine):
        print("число строк", th, "эталон", len(ref), "ядро", len(mine))
    for a, b in zip(ref, mine):
        if abs(float(a["t_start"]) - float(b["t_start"])) > 1e4:
            print("t_start не совпал", th, a["t_start"], b["t_start"]); continue
        for c, tol in COLS:
            x, y = float(a[c]), float(b[c])
            ok = (math.isnan(x) and math.isnan(y)) or abs(x - y) <= tol * max(1.0, abs(x))
            tot[c][0] += 1; tot[c][1] += ok
            if not ok:
                print("расхождение", th, c, a["t_start"], "эталон", x, "ядро", y)
print({c: "%d/%d" % (v[1], v[0]) for c, v in tot.items()})
