#!/usr/bin/env python3
"""Доллары и просадка клетка×месяц из rounds.csv полного счёта (тот же способ, что portfolio-sim: qty × entry_vwap × net_bps).
pnl_usd = Σ qty·entry_vwap·net_bps/1e4; max_dd_usd — макс. падение накопленного pnl_usd от пика по порядку exit_ns за месяц
(реализованное, без потолка позиций и без mark-to-market — не то же, что портфельный max_dd). Запуск — юнитом на сервере:
agg_tk040_usd.py <root> <out_dir> [мес…] -> <out_dir>/usd-<мес>.csv"""
import csv, os, sys, collections
from array import array
root, out = sys.argv[1], sys.argv[2]
months = sys.argv[3:] or sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d, "b5")))
os.makedirs(out, exist_ok=True)
for m in months:
    ex = collections.defaultdict(lambda: array("q"))
    pn = collections.defaultdict(lambda: array("d"))
    nt = collections.defaultdict(float)
    b5 = os.path.join(root, m, "b5")
    for grp in sorted(os.scandir(b5), key=lambda e: e.name):
        if not grp.is_dir() or grp.name.startswith("."):
            continue
        for day in os.scandir(grp.path):
            if not day.is_dir():
                continue
            for cell in os.scandir(day.path):
                fp = os.path.join(cell.path, "rounds.csv")
                if not os.path.isfile(fp):
                    continue
                with open(fp, encoding="utf-8", newline="") as f:
                    for r in csv.DictReader(l for l in f if not l.startswith("#")):
                        try:
                            usd = float(r["qty"]) * (float(r.get("entry_vwap") or 0) or float(r["entry_px"]))
                            k = (grp.name, cell.name, r["form"], day.name[:7])
                            ex[k].append(int(r["exit_ns"]))
                            pn[k].append(usd * float(r["net_bps"]) / 1e4)
                            nt[k] += usd
                        except (ValueError, KeyError):
                            continue
    with open(os.path.join(out, "usd-" + m + ".csv"), "w", encoding="utf-8", newline="\n") as o:
        w = csv.writer(o)
        w.writerow(["group", "set", "form", "month", "n_rounds", "pnl_usd", "max_dd_usd", "notional_usd"])
        for k in sorted(ex):
            order = sorted(range(len(ex[k])), key=ex[k].__getitem__)
            cum = peak = dd = 0.0
            for i in order:
                cum += pn[k][i]
                peak = max(peak, cum)
                dd = max(dd, peak - cum)
            w.writerow(list(k) + [len(order), "%.4f" % cum, "%.4f" % dd, "%.2f" % nt[k]])
    print(m, len(ex), flush=True)
