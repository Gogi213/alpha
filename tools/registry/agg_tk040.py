#!/usr/bin/env python3
"""Опись выходов полного счёта: <root>/<мес>/b5/<группа>/<сутки>/<набор>/forms.csv -> по (набор, форма, месяц) одна строка.
Запуск — на сервере счёта юнитом (nice, CPUQuota): agg_tk040.py <root> <out_dir> [мес…]."""
import csv, os, sys, collections, json
root, out = sys.argv[1], sys.argv[2]
months = sys.argv[3:] or sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d, "b5")))
os.makedirs(out, exist_ok=True)
NUM = ["n_signals", "n_fills", "n_stop", "n_take", "n_trail", "n_deadline"]
for m in months:
    agg = collections.defaultdict(lambda: collections.defaultdict(float))
    days = collections.defaultdict(set)
    nfiles = 0
    b5 = os.path.join(root, m, "b5")
    for grp in sorted(os.scandir(b5), key=lambda e: e.name):
        if not grp.is_dir() or grp.name.startswith("."):
            continue
        for day in os.scandir(grp.path):
            if not day.is_dir():
                continue
            for cell in os.scandir(day.path):
                fp = os.path.join(cell.path, "forms.csv")
                if not os.path.isfile(fp):
                    continue
                nfiles += 1
                with open(fp, encoding="utf-8", newline="") as f:
                    rd = csv.DictReader(l for l in f if not l.startswith("#"))
                    for r in rd:
                        k = (grp.name, cell.name, r["form"], day.name[:7])
                        a = agg[k]
                        a["symdays"] += 1
                        a["sum_net_bps"] += float(r["sum_net_bps"] or 0)
                        for c in NUM:
                            a[c] += float(r.get(c) or 0)
                        days[k].add(day.name)
    with open(os.path.join(out, m + ".csv"), "w", encoding="utf-8", newline="\n") as o:
        w = csv.writer(o)
        w.writerow(["group", "set", "form", "month", "days", "symdays", "sum_net_bps"] + NUM)
        for k in sorted(agg):
            a = agg[k]
            w.writerow(list(k) + [len(days[k]), int(a["symdays"]), "%.6f" % a["sum_net_bps"]] + [int(a[c]) for c in NUM])
    print(m, nfiles, len(agg), flush=True)
