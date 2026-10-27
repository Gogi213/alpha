#!/usr/bin/env python3
"""TK-415: таблица стаканов делистов за 2026-09 по результату закачки tk415-ob-dl.py.
Использование: tk415-delist-table.py <каталог выхода> <список ex<TAB>mk<TAB>sym>  -> md-таблица в stdout.
Дни: «есть» = файл на диске; «нет» = строка в _missing.tsv (404 = в архиве нет); остальное — не получено."""
import os
import sys

outd, listf = sys.argv[1:3]
miss = {}
for ln in open(os.path.join(outd, "_missing.tsv"), encoding="utf-8"):
    p = ln.split()
    if len(p) == 4:
        miss.setdefault((p[1], p[2]), set()).add(p[3])
print("| рынок | инструмент | файлов (дней) | ГБ | 404 (дней нет) | дни с файлами | не получено |")
print("|---|---|---|---|---|---|---|")
tf = tb = tm = tu = 0
for ln in open(listf, encoding="utf-8"):
    f = ln.rstrip("\n").split("\t")
    if len(f) != 3:
        continue
    _, mk, s = f
    d = os.path.join(outd, "bybit-" + mk, s)
    names = sorted(x for x in os.listdir(d) if x.endswith(".zip")) if os.path.isdir(d) else []
    days = [x[8:10] for x in names]
    gb = sum(os.path.getsize(os.path.join(d, x)) for x in names) / 1e9
    m = len(miss.get((mk, s), ()))
    un = 30 - len(names) - m
    rng = "%s–%s" % (days[0], days[-1]) if days else "—"
    print("| bybit %s | %s | %d | %.2f | %d | %s | %d |" % (mk, s, len(names), gb, m, rng, un))
    tf += len(names)
    tb += gb
    tm += m
    tu += un
print("| **итого** | | **%d** | **%.2f** | **%d** | | **%d** |" % (tf, tb, tm, tu))
