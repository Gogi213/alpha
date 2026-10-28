#!/usr/bin/env python3
"""TK-439: cProfile tk439-vol.one на первых N инструментах одного вол-файла (1 процесс). Использование: tk439-volprof.py <windows.csv> <D> <X> <куб> <cut> [N]"""
import cProfile, csv, importlib.util, pstats, sys, time
from collections import defaultdict
sp = importlib.util.spec_from_file_location("v439", "/data/tk0435/tk439-vol.py")
V = importlib.util.module_from_spec(sp); sys.modules["v439"] = V; sp.loader.exec_module(V)
M = V.M
wf, D, X, cube, cut = sys.argv[1:6]
N = int(sys.argv[6]) if len(sys.argv) > 6 else 40
M.CUBE, M.CUT = cube, int(cut)
by = defaultdict(list)
for r in csv.DictReader(open(wf, encoding="utf-8")):
    if r["исключено"] == "" and r["представитель"] == "1" and int(r["D"]) == int(D) and float(r["X"]) == float(X):
        by[("%s-%s" % (r["биржа"], r["рынок"]), r["инструмент"])].append(r)
jobs = sorted(by.items(), key=lambda kv: -len(kv[1]))[:N]
print(len(by), "инстр.;", N, "взято;", sum(len(v) for _, v in jobs), "событий из", sum(len(v) for v in by.values()))
pr = cProfile.Profile(); t = time.time(); pr.enable()
for (s, i), v in jobs:
    V.one((s, i, v, []))
pr.disable(); print("wall", round(time.time() - t, 2))
pstats.Stats(pr).sort_stats("tottime").print_stats(14)
