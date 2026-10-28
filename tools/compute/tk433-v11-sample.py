#!/usr/bin/env python3
"""TK-433 v11: случайная стратифицированная выборка событий для v9b-признаков (Судья 28.10 07:12).
Ячейка = месяц x D x знак; до CAP представителей равномерно случайно, SEED до счёта. Выход — «ноги» для tk433-features.py windows
(колонка θ = D; представитель=1 только у выбранных; остальные события — занятость для контролей).
sample.py <pump-v11-events-ГГГГ-ММ.csv> <выход.csv>"""
import csv, random, sys
from collections import defaultdict
SEED, CAP = 433, 600
rows = list(csv.DictReader(open(sys.argv[1], encoding="utf-8")))
cells = defaultdict(list)
for i, r in enumerate(rows):
    cells[(r["D_мин"], r["знак"])].append(i)
chosen = set()
rng = random.Random(SEED)
for k in sorted(cells):
    ix = cells[k]
    chosen.update(rng.sample(ix, CAP) if len(ix) > CAP else ix)
    print("D=%s %s: N=%d выбрано=%d" % (k[0], k[1], len(ix), min(len(ix), CAP)), file=sys.stderr)
with open(sys.argv[2], "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f, lineterminator="\n")
    w.writerow(["биржа", "рынок", "инструмент", "знак", "t_start_ms", "t_peak_ms", "исключено", "представитель", "θ"])
    for i, r in enumerate(rows):
        ex, mk = r["биржа_рынок"].split("-", 1)
        w.writerow([ex, mk, r["инструмент"], r["знак"], r["t_start_ms"], r["t_peak_ms"], "", "1" if i in chosen else "0", r["D_мин"]])
