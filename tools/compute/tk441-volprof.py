#!/usr/bin/env python3
"""TK-441 круг 3: профиль вол-задания (tk439-volall.run) на одном инструменте, 1 процесс.
  tk441-volprof.py <windows.csv> <xq.json> <SUB> <SYM> --cube <куб> --cut <мин>"""
import cProfile
import os
import csv
import json
import os
import pstats
import sys
import time
from collections import defaultdict

D0 = os.environ.get("VOLDIR", "/data/tk0435")
import importlib.util

sp = importlib.util.spec_from_file_location("volall", D0 + "/tk439-volall.py")
VA = importlib.util.module_from_spec(sp)
sys.modules["volall"] = VA
sp.loader.exec_module(VA)
av = sys.argv
VA.M.CUBE = av[av.index("--cube") + 1]
VA.M.CUT = int(av[av.index("--cut") + 1])
wf, xqf, SUB, SYM = av[1:5]
xq = json.load(open(xqf))
wins = list(csv.DictReader(open(wf, encoding="utf-8")))
parts = defaultdict(list)
for D in (90, 180, 360, 720, 1440):
    for sg, nm in (("+", "p"), ("-", "m")):
        X = float(xq["%d|%s" % (D, sg)]["q99"])
        for r in wins:
            if r["исключено"] == "" and r["представитель"] == "1" and int(r["D"]) == D and float(r["X"]) == X and "%s-%s" % (r["биржа"], r["рынок"]) == SUB and r["инструмент"] == SYM:
                parts[(D, nm)].append(r)
job = (SUB, SYM, sorted(parts.items()), [])
TV = defaultdict(lambda: [0, 0.0, 0])
_v = VA.SC.vals


def _timed(self, var, es):
    t0 = time.perf_counter()
    r = _v(self, var, es)
    a = TV[var]
    a[0] += 1
    a[1] += time.perf_counter() - t0
    a[2] += len(es)
    return r


VA.SC.vals = _timed
r0 = VA.run(job)  # прогрев (загрузка куба в кэш страниц, компиляция numba)
if os.environ.get("NOPROF"):
    import hashlib
    t = time.time()
    r1 = VA.run(job)
    print("md5 %s стена %.2f с" % (hashlib.md5(repr(r1).encode()).hexdigest(), time.time() - t))
    sys.exit(0)
t = time.time()
pr = cProfile.Profile()
pr.enable()
VA.run(job)
pr.disable()
print("стена %.2f с" % (time.time() - t))
pstats.Stats(pr).sort_stats("tottime").print_stats(8)
for v, (n, sec, pts) in sorted(TV.items(), key=lambda x: -x[1][1]):
    print("%-8s вызовов %4d  точек %7d  %.3f с  (%.1f мкс/точка)" % (v, n, pts, sec, sec / max(pts, 1) * 1e6))
