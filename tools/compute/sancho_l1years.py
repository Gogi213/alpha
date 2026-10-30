#!/usr/bin/env python3
"""TK-451 (возврат Судьи 30.10): слой 1 по годам и по ранним/поздним монетам — по готовым таблицам l1 (без пересчёта событий, В-213).
python sancho_l1years.py <каталог l1> <выход.csv> <k>   (1H; якорь N=10; ячейки как в sancho_l1agg.cells, CI по дням)
Подмножества: all84 по годам UTC 2020…2026; early (монеты с событиями в 2020) / late (остальные) за весь период."""
import sys
import pandas as pd
import sancho_l1agg as A

A.NS = (10,)
dirp, outp, k = sys.argv[1], sys.argv[2], int(sys.argv[3])
d, nf, nbad = A.load(dirp, 60, k)
d["year"] = pd.to_datetime(d.t, unit="ms").dt.year
first = d.groupby("sym", observed=True).t.min()
early = set(first[pd.to_datetime(first, unit="ms").dt.year <= 2020].index)
d["grp"] = d.sym.astype(str).isin(early).map({True: "early", False: "late"})
print("load", k, nf, len(d), nbad, "early", len(early), flush=True)
rows = []
for y, g in d.groupby("year"):
    rows += [dict(subset=f"y{y}", nsym=g.sym.nunique(), **r) for r in A.cells(g.reset_index(drop=True), 60, k, "all", True)]
    pd.DataFrame(rows).to_csv(outp, index=False)
for gname, g in d.groupby("grp"):
    rows += [dict(subset=gname, nsym=g.sym.nunique(), **r) for r in A.cells(g.reset_index(drop=True), 60, k, "all", True)]
pd.DataFrame(rows).to_csv(outp, index=False)
print("DONE", len(rows))
