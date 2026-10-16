#!/usr/bin/env python3
"""TK-268 ш.2: сверка signals.csv новой пробы (с колонками 60s) со старым пулом TK-064: старые колонки побайтно, новые — доля определённых.
   tk268-g82-cmp.py <старый signals.csv> <новый signals.csv>"""
import csv
import sys

NEW = ("tape_burst_press_60s_bp", "tape_burst_with_60s_bp")


def load(p):
    with open(p, newline="", encoding="utf-8") as f:
        r = csv.reader(x for x in f if not x.startswith("#"))   # первая строка — «# lob bounce-grid: …»
        return next(r), list(r)


oh, orows = load(sys.argv[1])
nh, nrows = load(sys.argv[2])
extra = [c for c in nh if c not in oh]
missing = [c for c in oh if c not in nh]
print(f"строк старых {len(orows)} новых {len(nrows)}; колонок {len(oh)} → {len(nh)}; добавлено {extra}; пропало {missing}")
idx = [nh.index(c) for c in oh if c in nh]
same = len(orows) == len(nrows) and all([r[i] for i in idx] == o for o, r in zip(orows, nrows) if len(r) == len(nh))
print("старые колонки равны:", same)
for c in NEW:
    if c in nh:
        i = nh.index(c)
        defined = sum(1 for r in nrows if r[i] != "")
        print(f"{c}: определено {defined} из {len(nrows)}")
sys.exit(0 if same and set(extra) <= set(NEW) and not missing else 1)
