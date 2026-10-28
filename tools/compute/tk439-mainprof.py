#!/usr/bin/env python3
"""TK-439: профиль main() tk435-windows.py (главный процесс): tk439-mainprof.py <модуль.py> <аргументы main...>"""
import cProfile
import importlib.util
import pstats
import sys
import time

sp = importlib.util.spec_from_file_location("w", sys.argv[1])
w = importlib.util.module_from_spec(sp)
sys.modules["w"] = w
sp.loader.exec_module(w)
sys.argv = [sys.argv[1]] + sys.argv[2:]
pr = cProfile.Profile()
T = time.time()
pr.enable()
w.main()
pr.disable()
print("стена main %.1f с" % (time.time() - T))
pstats.Stats(pr).sort_stats("tottime").print_stats(18)
