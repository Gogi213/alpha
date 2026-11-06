#!/usr/bin/env python3
"""TK-493 F7 (Deck, stdlib): проверка файлов заданий волн: все панели торговли и файлы фандинга существуют, окно заданий 2479..2500, число строк.
Запуск: python3 sel_check_jobs.py ~/xdl9/sel/waves"""
import csv, glob, os, sys

root = sys.argv[1]
home = os.path.expanduser("~/xdl9")
bad = tot = nj = 0
for jf in sorted(glob.glob(os.path.join(root, "w*", "*.jobs.csv"))):
    name = os.path.basename(jf)[:-9]
    exy, exx = name.split("__")[1], name.split("__")[2]
    with open(jf, encoding="utf-8", newline="") as fh:
        rd = csv.reader(fh)
        hdr = next(rd)
        assert hdr == ["y", "x", "from_day", "to_day"], jf
        for y, x, a, b in rd:
            nj += 1
            assert (a, b) == ("2479", "2500"), (jf, a, b)
            for ex, stems in ((exy, [y]), (exx, x.split("+"))):
                for st in stems:
                    tot += 1
                    if not os.path.exists(os.path.join(home, "trade", ex, st + ".sap")):
                        bad += 1
                        print("нет панели", ex, st, jf)
print("заданий", nj, "проверено панелей", tot, "нет", bad)
