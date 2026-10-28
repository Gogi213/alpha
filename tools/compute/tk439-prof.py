#!/usr/bin/env python3
"""TK-439: профиль one() детектора окон TK-435 на кубе: N инструментов каждого рынка, месяц, X=5 %, D=90..1440.
  tk439-prof.py <каталог со скриптами tk435> <куб> <l4v8.json> <art.json> <N> [модуль-детектора.py]"""
import cProfile
import importlib.util
import json
import os
import pstats
import sys
import time

HD, CUBE, L4, ART, N = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5])
mod = sys.argv[6] if len(sys.argv) > 6 else "tk435-windows.py"
sp = importlib.util.spec_from_file_location("w", os.path.join(HD, mod))
w = importlib.util.module_from_spec(sp)
sp.loader.exec_module(w)
w.mv.load_excl(ART)
w.CUBE = CUBE
w.CFG["d"] = [90, 180, 360, 720, 1440]
w.CFG["x"] = [0.05]
t0, t1 = w.mv.month_bounds("2026-09")
args = []
for ex, mk, sub in w.mv.MKT:
    f = "%s/%s.syms" % (CUBE, sub)
    if not os.path.exists(f):
        continue
    syms = sorted(open(f).read().split())
    syms = syms[:: max(1, len(syms) // N)][:N]
    args += [(ex, mk, os.path.join("-", sub), s, t0, t1) for s in syms]
pr = cProfile.Profile()
T = time.time()
pr.enable()
nrow = 0
for a in args:
    r, h = w.one(a)
    nrow += len(r)
pr.disable()
print("инстр", len(args), "строк", nrow, "стена %.1f с" % (time.time() - T))
pstats.Stats(pr).sort_stats("cumtime").print_stats(14)
