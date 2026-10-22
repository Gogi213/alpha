#!/usr/bin/env python3
"""TK-336: надстройка П-23 (σ240 < 94,2 bps) на готовых rounds решётки TK-289 (f6v), клетки помимо B1 (p18a6vv2_10000).
Без нового прохода рынка: σ240 — as-of join по t0 из готовых sigma240 (как tk327-features.py fut). Σ$ = net_bps*qty*px/1e4
по сырым rounds клетки (без psim-капов). Запуск (calc): p23-overlay.py <out.csv>"""
import csv, glob, os, re, sys, bisect
from collections import defaultdict
TH = 94.2
OUT = sys.argv[1]
agg = defaultdict(lambda: [0, 0, 0.0, 0.0, 0])   # (cell, mon) -> n, ncut, sum_all, sum_cut, nosig
cache = {}
MONS = {"01": "jan", "02": "feb", "03": "mar", "04": "apr", "05": "may", "06": "jun", "07": "jul", "08": "aug", "09": "sep"}


def sig(mo, sym):
    k = (mo, sym)
    if k not in cache:
        p = f"/data/tk046/{mo}/home/alpha/epochs/e-{mo}/study/sigma240/sigma-{sym}.csv"
        xs, vs = [], []
        if os.path.exists(p):
            with open(p, newline="") as f:
                for r in csv.DictReader(l for l in f if not l.startswith("#")):
                    xs.append(int(r["window_end_ms"])); vs.append(float(r["sigma_bps"]))
        cache[k] = (xs, vs)
    return cache[k]


for p in sorted(glob.glob("/data/tk0289/f6v/out/run-*/out/*/*/rounds.csv")):
    parts = p.split("/")
    day, cell = parts[-3], re.sub(r"-\d{4}-\d{2}$", "", parts[-2])
    if cell == "p18a6vv2_10000":
        continue
    mon = day[:7]; mo = MONS[day[5:7]]
    with open(p, newline="") as f:
        rows = list(csv.DictReader(l for l in f if not l.startswith("#")))
    a = agg[(cell, mon)]
    for r in rows:
        v = float(r["net_bps"]) * float(r["qty"]) * float(r["entry_px"]) / 1e4
        xs, vs = sig(mo, r["symbol"]); t0 = int(r["t0_ns"]) // 1_000_000
        i = bisect.bisect_right(xs, t0) - 1
        a[0] += 1; a[2] += v
        if i < 0:
            a[4] += 1
        elif vs[i] < TH:
            a[1] += 1; a[3] += v
with open(OUT, "w", newline="") as f:
    w = csv.writer(f); w.writerow(["cell", "month", "n", "n_cut", "n_nosigma", "sum_all", "sum_filt", "sum_cut"])
    for (c, m), a in sorted(agg.items()):
        w.writerow([c, m, a[0], a[1], a[4], round(a[2], 1), round(a[2] - a[3], 1), round(a[3], 1)])
print(len(agg), "cell-months")
