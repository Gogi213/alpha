#!/usr/bin/env python3
"""TK-609: замер фаз полного прохода `retitr_ftab.py build` (стена и ЦП по фазам: load_prod, build_episodes, run_tk594, остальное).
  python3 tk609_pass_time.py <каталог кода с tools/p42/retitr_ftab.py> -- <аргументы retitr_ftab: build S --sigsx … --work … --out … --force>"""
import json
import os
import resource
import sys
import time

code = sys.argv[1]
args = sys.argv[sys.argv.index("--") + 1:]
sys.path.insert(0, os.path.join(code, "tools", "p42"))
import retitr_ftab as R

PH = {}


def cpu():
    s, c = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)
    return s.ru_utime + s.ru_stime + c.ru_utime + c.ru_stime


def timed(name, fn):
    def w(*a, **k):
        t0, c0 = time.time(), cpu()
        try:
            return fn(*a, **k)
        finally:
            PH[name] = {"wall_s": round(time.time() - t0, 2), "cpu_s": round(cpu() - c0, 2)}
    return w


for n in ("load_prod", "build_episodes", "run_tk594"):
    setattr(R, n, timed(n, getattr(R, n)))
T0, C0 = time.time(), cpu()
R.main(args)
PH["total"] = {"wall_s": round(time.time() - T0, 2), "cpu_s": round(cpu() - C0, 2)}
print("PHASES " + json.dumps(PH), flush=True)
