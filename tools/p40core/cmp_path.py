"""TK-500: сверка <out>.path.csv ядра (МПУ по исполнимой стороне, лучший, итог на конец) с episodes_<θ>.csv эталона TK-501 (колонки 8–11) по (θ, t_start).
Запуск: cmp_path.py <core.path.csv> <каталог эталона ep> <θ> ..."""
import csv, math, sys
import numpy as np

core = list(csv.DictReader(open(sys.argv[1])))
tot = {"mpu": [0, 0], "t_mpu": [0, 0], "best": [0, 0], "pnl_end": [0, 0]}
for th in sys.argv[3:]:
    e = np.loadtxt(f"{sys.argv[2]}/episodes_{th}.csv", delimiter=",", skiprows=1, ndmin=2)
    ref = {int(r[0]): r for r in e}
    c = [r for r in core if abs(float(r["theta"]) - float(th)) < 1e-9]
    print("theta", th, "эталон", len(ref), "ядро", len(c))
    for r in c:
        t0 = int(r["t0_ms"]); x = ref.get(t0)
        if x is None:
            print("  нет в эталоне", t0); continue
        for k, v, w, tol in (("mpu", float(r["mpu_bps"]), x[8], 1e-3), ("t_mpu", float(r["t_mpu_ms"]), x[9], 0), ("best", float(r["best_bps"]), x[10], 1e-3), ("pnl_end", float(r["pnl_end_bps"]), x[11], 1e-3)):
            ok = (math.isnan(v) and math.isnan(w)) or abs(v - w) <= tol
            tot[k][0] += 1; tot[k][1] += ok
            if not ok:
                print("  расхождение", th, t0, k, "ядро", v, "эталон", w)
print({k: "%d/%d" % (v[1], v[0]) for k, v in tot.items()})
