#!/usr/bin/env python3
"""TK-422: окна пробы из размахов TK-414/420 (соседние точки разворота: t_start=p[i], t_peak=p[i+1], t_end=p[i+2]).
Использование: tk422-probe-windows.py <legs.csv>[:<с>:<по>:<инстр,инстр>] ... > окна.csv   (с/по — даты ГГГГ-ММ-ДД, по начало_utc)"""
import csv, sys

out = csv.writer(sys.stdout, lineterminator="\n")
out.writerow(["id", "биржа", "рынок", "инструмент", "t_start", "t_peak", "t_end", "знак"])
n = 0
for spec in sys.argv[1:]:
    p = spec.split(":")
    d0, d1 = (p[1], p[2]) if len(p) > 2 else ("0000", "9999")
    only = set(p[3].split(",")) if len(p) > 3 else None
    legs = {}
    for r in csv.DictReader(open(p[0], encoding="utf-8")):
        if only is None or r["инструмент"] in only:
            legs.setdefault((r["биржа"], r["рынок"], r["инструмент"]), []).append(r)
    for (ex, mk, sym), L in sorted(legs.items()):
        L.sort(key=lambda r: r["начало_utc"])
        pts = [r["начало_utc"] for r in L] + [L[-1]["пик_utc"]]
        for i in range(len(L) - 1):
            if d0 <= L[i]["начало_utc"][:10] <= d1:
                out.writerow([n, ex, mk, sym, pts[i], pts[i + 1], pts[i + 2], L[i]["знак"]])
                n += 1
