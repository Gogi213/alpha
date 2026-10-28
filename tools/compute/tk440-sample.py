#!/usr/bin/env python3
"""TK-440: windows-ГГГГ-ММ.csv (tk435-windows events, представитель=1) -> формат событий v11 -> выборка tk433-v11-sample.py (сид 433, ≤600/ячейку).
tk440-sample.py <windows.csv> <выход legs.csv> <каталог с tk433-v11-sample.py>"""
import csv, subprocess, sys, tempfile, os
rows = [r for r in csv.DictReader(open(sys.argv[1], encoding="utf-8")) if r["представитель"] == "1"]
tmp = tempfile.mktemp(suffix=".csv")
with open(tmp, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f, lineterminator="\n")
    w.writerow(["D_мин", "id", "биржа_рынок", "инструмент", "знак", "t_start_ms", "t_peak_ms", "длит_мин", "контролей", "история_сут"])
    for r in rows:
        w.writerow([r["D"], "", r["биржа"] + "-" + r["рынок"], r["инструмент"], r["знак"], r["t_start_ms"], r["t_peak_ms"], r["длит_мин"], "", ""])
subprocess.check_call([sys.executable, os.path.join(sys.argv[3], "tk433-v11-sample.py"), tmp, sys.argv[2]])
os.remove(tmp)
