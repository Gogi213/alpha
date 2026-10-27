#!/usr/bin/env python3
"""TK-420 вар. 3: добавить D4 (tk420-var3-d4.py) к d3-A.csv колонками tau_ms, d4, thr4, nbase4, sel_D4 (по ключу инструмент+t_событие+знак).
Использование: tk420-var3-d4-merge.py <d3-A.csv> <каталог *.d4.csv> <выход.csv>"""
import csv, glob, os, sys
src, d4dir, outp = sys.argv[1:4]
d4 = {}
for fn in glob.glob(os.path.join(d4dir, "*.d4.csv")):
    for r in csv.DictReader(open(fn, newline="", encoding="utf-8")):
        d4[(r["инструмент"], r["t_событие"], r["знак"])] = r
rows = list(csv.DictReader(open(src, newline="", encoding="utf-8")))
for r in rows:
    x = d4.get((r["инструмент"], r["t_событие"], r["знак"]))
    r["tau_ms"] = x["tau_ms"] if x else ""
    r["d4"] = x["d4"] if x else ""
    r["thr4"] = x["thr4"] if x else ""
    r["nbase4"] = x["nbase"] if x else ""
    r["sel_D4"] = x["sel_D4"] if x else ""
with open(outp, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, list(rows[0].keys())); w.writeheader(); w.writerows(rows)
print("событий %d, D4 есть %d, выбрано %d" % (len(rows), sum(1 for r in rows if r["d4"] != ""), sum(1 for r in rows if r["sel_D4"] == "1")))
