#!/usr/bin/env python3
"""TK-466: сверка числа событий/флагов s-1..s-5 с воронками P-TK459 (docs/findings/smartmoney-synthesis-S<n>-2026-10-30-funnel.csv).
python check_tk466_s_counts.py <каталог s-файлов> <docs/findings> [<sm_s6 -chains.csv>] -> таблица «наше / P-TK459 / файл:строка», rc 1 при любом расхождении."""
import sys
import numpy as np
import pandas as pd

OUT, F = sys.argv[1].rstrip("/"), sys.argv[2].rstrip("/")
fun = lambda n: pd.read_csv(f"{F}/smartmoney-synthesis-S{n}-2026-10-30-funnel.csv")
rows = []


def add(n, label, ours, ref, line):
    rows.append((f"S{n}", label, int(ours), int(ref), int(ours) == int(ref), f"smartmoney-synthesis-S{n}-2026-10-30-funnel.csv:{line}"))


s = pd.read_csv(f"{OUT}/s-1.csv.gz")
for i, r in fun(1).iterrows():
    nm, d = r["строка"].split(" ")[1], r["строка"][-1]
    g = s[(s.tf == 60) & (s.dir == d)]
    add(1, r["строка"] + " события", len(g), r["событий"], i + 2)
    add(1, r["строка"] + " с фактором", g[nm].sum(), r["с фактором"], i + 2)

s = pd.read_csv(f"{OUT}/s-2.csv.gz")
for i, r in fun(2).iterrows():
    t, d, v = r["строка"].split(" ")[1].split("/")[0], r["строка"].split("/")[1], r["строка"].split("/")[2]
    g = s[(s.tf == {"1H": 60, "4H": 240, "1D": 1440}[t]) & (s.dir == d)]
    c = "a" if v == "ТФ свипа" else "b"
    add(2, r["строка"] + " события", len(g), r["событий"], i + 2)
    add(2, r["строка"] + " с флагом", g["prem_" + c].notna().sum(), r["с флагом"], i + 2)
    add(2, r["строка"] + " флаг=1", (g["prem_" + c] == 1).sum(), r["премиум/дискаунт-флаг=1"], i + 2)

s = pd.read_csv(f"{OUT}/s-3.csv.gz")
seen = set()
for i, r in fun(3).iterrows():
    p = r["строка"].split(" ")[1].split("/")[0], r["строка"].split("/")[1], 2 if "Санчо" in r["строка"] else 1
    if p in seen:
        continue
    seen.add(p)
    g = s[(s.tf == {"1H": 60, "4H": 240, "1D": 1440}[p[0]]) & (s.dir == p[1]) & (s.anchor == p[2])]
    add(3, f"{p[0]}/{p[1]}/k={p[2]} события A6", len(g), r["событий A6"], i + 2)
    add(3, f"{p[0]}/{p[1]}/k={p[2]} из них A8", g.a8.sum(), r["из них A8 (слом)"], i + 2)
    add(3, f"{p[0]}/{p[1]}/k={p[2]} с контролем C2 (p459=1)", g.p459.sum(), r["с контролем C2"], i + 2)

s = pd.read_csv(f"{OUT}/s-4.csv.gz")
for i, r in fun(4).iterrows():
    a, d = r["строка"].split(" ")[1].split("→"), r["строка"][-1]
    a = a[0], a[1].split("/")[0]
    g = s[(s.tf == {"1H": 60, "4H": 240, "1D": 1440}[a[0]]) & (s.dir == d)]
    add(4, r["строка"] + " события", len(g), r["событий"], i + 2)
    add(4, r["строка"] + " с флагом", g["p" + a[1]].notna().sum(), r["с флагом"], i + 2)
    add(4, r["строка"] + " фактор=1", (g["p" + a[1]] == 1).sum(), r["фактор=1 (цель-фрактал противоп. типа)"], i + 2)

s = pd.read_csv(f"{OUT}/s-5.csv.gz")
# P-TK459 считает по суткам Азии (одна запись на сутки), s-5 - по событиям A5: суток = уникальных (монета, сторона, час-бар свипа)
s["hk"] = s.tj // 3600000
for i, r in fun(5).iterrows():
    if "d=0,25" not in r["строка"]:
        continue
    d = r["строка"].split()[2]
    g = s[s.dir == d]
    add(5, f"Азия {d} суток со свипом в час (s5=1)", g[g.s5 == 1].drop_duplicates(["sym", "hk"]).shape[0], r["со свипом в час"], i + 2)
    add(5, f"Азия {d} исключено с фактором (s5x=1)", g[g.s5x == 1].drop_duplicates(["sym", "hk"]).shape[0], r["исключено с фактором"], i + 2)

if len(sys.argv) > 3:                       # S6: метки sm_s6 (принятый код) против колонок s-6 по id цепочки; 71 цепочка, цель1 первой 48 не в файле (исход не пишется)
    s6 = pd.read_csv(f"{OUT}/s-6.csv.gz")
    ref = pd.read_csv(sys.argv[3])
    x = ref.merge(s6, on="id", suffixes=("_r", ""))
    add(6, "цепочек в s-6 / в sm_s6 (71 по TK-454)", len(s6), 71, "-")
    add(6, "id совпали с sm_s6", len(x), len(ref), "-")
    for nm, a, b in (("d1", "d1_r", "d1"), ("prem", "prem_r", "prem"), ("сессия Азия", "сессия Азия", "session_asia"), ("сессия Франкфурт", "сессия Франкфурт", "session_frankfurt"),
                     ("сессия Лондон", "сессия Лондон", "session_london"), ("сессия Нью-Йорк", "сессия Нью-Йорк", "session_ny")):
        u, v = x[a].values.astype(float), x[b].values.astype(float)
        add(6, f"расхождений метки {nm}", (~((u == v) | (np.isnan(u) & np.isnan(v)))).sum(), 0, "-")

T = pd.DataFrame(rows, columns=["слой", "строка", "наше", "P-TK459", "совпало", "файл:строка"])
print(T.to_string(index=False))
print("совпало", int(T.совпало.sum()), "из", len(T))
sys.exit(0 if T.совпало.all() else 1)
