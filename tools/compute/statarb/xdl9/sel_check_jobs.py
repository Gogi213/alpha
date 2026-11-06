#!/usr/bin/env python3
"""TK-493 S1 (Steam Deck, stdlib): проверка файлов заданий волн: все панели торговли и файлы фандинга существуют (проверяется ТОЛЬКО наличие файлов),
окно заданий 2479..2500, число строк; у D2 - списки пар (задания по шардам пишет d2_diff_funding.py на этапе волны); выходов счёта нет.
Запуск: python3 sel_check_jobs.py ~/xdl9/sel/waves"""
import csv, glob, os, sys

root = sys.argv[1]
home = os.path.expanduser("~/xdl9")
bad = tot = nj = nf = 0
for jf in sorted(glob.glob(os.path.join(root, "w[123]", "*.jobs.csv")) + glob.glob(os.path.join(root, "w[123]", "*.pairs.csv"))):
    name = os.path.basename(jf).rsplit(".", 2)[0]
    exy, exx = name.split("__")[1], name.split("__")[2]
    is_pairs = jf.endswith(".pairs.csv")
    with open(jf, encoding="utf-8", newline="") as fh:
        rd = csv.reader(fh)
        hdr = next(rd)
        assert hdr == (["y", "x"] if is_pairs else ["y", "x", "from_day", "to_day"]), jf
        for row in rd:
            nj += 1
            if not is_pairs:
                assert (row[2], row[3]) == ("2479", "2500"), (jf, row)
            for ex, stems in ((exy, [row[0]]), (exx, row[1].split("+"))):
                for st in stems:
                    tot += 1
                    if not os.path.exists(os.path.join(home, "trade", ex, st + ".sap")):
                        bad += 1
                        print("нет панели", ex, st, jf)
                    if is_pairs and not os.path.exists(os.path.join(home, "funding", ex, st + ".csv.gz")):
                        nf += 1
                        print("нет файла фандинга", ex, st, jf)
outs = [p for p in (os.path.join(root, "out"), os.path.join(home, "out"), os.path.join(root, "d2")) if os.path.exists(p)]
print("заданий/пар", nj, "проверено панелей", tot, "нет панелей", bad, "D2: нет файла фандинга", nf, "выходы счёта (должны отсутствовать):", outs)
