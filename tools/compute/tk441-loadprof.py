#!/usr/bin/env python3
"""TK-441 круг 3: из чего состоит фикс. стоимость вол-задания на инструмент — load_cube vs Series.__init__ (горячий кэш, 2-й проход).
  tk441-loadprof.py <куб> <cut> <SUB> [N]"""
import importlib.util
import sys
import time

import numpy as np

sp = importlib.util.spec_from_file_location("tk435v", "/data/tk0435/tk435-vol.py")
M = importlib.util.module_from_spec(sp)
sys.modules["tk435v"] = M
sp.loader.exec_module(M)
M.CUBE, M.CUT = sys.argv[1], int(sys.argv[2])
sub = sys.argv[3]
N = int(sys.argv[4]) if len(sys.argv) > 4 else 40
M.load_cube(sub, "x")
syms = M._MM[sub][1][:N]
shape = M._MM[sub][0].shape
print("куб", sub, shape, "инстр.", len(M._MM[sub][1]))
for p in (1, 2):
    tl = ts = tm = 0.0
    for s in syms:
        t = time.perf_counter()
        ld = M.load_cube(sub, s)
        tl += time.perf_counter() - t
        if ld is None:
            continue
        t = time.perf_counter()
        M.Series(*ld)
        ts += time.perf_counter() - t
    print("проход %d: load_cube %.1f мс/инстр, Series %.1f мс/инстр (n=%d)" % (p, tl / len(syms) * 1e3, ts / len(syms) * 1e3, len(syms)))
