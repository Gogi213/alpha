#!/usr/bin/env python3
"""TK-440: tk438-scen.py на ОДНОМ месяце янв–июль (признаки f-ММ, свечи месяца), те же флаги/пороги по контролям ячейки.
Использование: [MODE=shares|agg NB=..] tk440-scen.py <ММ> <features.csv> <xq.json> <свечи-каталог> <выход-префикс>
Месяц в строке выхода — «ММ»; реплика исходника tk438-scen.py с подменой списка месяцев (код счёта не форкается)."""
import sys, os
MM, F, XQ_, ROOT, OUTP = sys.argv[1:6]
here = os.path.dirname(os.path.abspath(__file__))
src = open(os.path.join(here, "tk438-scen.py"), encoding="utf-8").read()
old = 'for mon, F, root in (("09", F9, C9_), ("08", F8, C8_)):'
assert old in src
src = src.replace(old, 'for mon, F, root in ((MM, F9, C9_),):')
sys.argv = ["tk438-scen.py", F, F, XQ_, ROOT, ROOT, OUTP]
g = {"__name__": "__main__", "MM": MM, "__file__": os.path.join(here, "tk438-scen.py")}
exec(compile(src, "tk438-scen.py", "exec"), g)
