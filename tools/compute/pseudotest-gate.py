#!/usr/bin/env python3
"""TK-363 гейт: pseudotest-cells.csv на двух гипотезах = g015-cells.csv (TK-355) до округления.
  pseudotest-gate.py <pseudotest-cells.csv> <g015-cells.csv> [--hyp-first ID --hyp-rep ID]
Соответствие: first_lt100 ↔ (touch=first, strength=lt100), rep_ge1000 ↔ (touch=rep, strength=ge1000). Сверяются n, n_days,
m_* и favour/adverse (round(·,3) — как в g015); расхождение в одну единицу последнего знака при |Δ|<1,5e-3 — «ничья округления»
(порядок суммирования), считается отдельно и не провал; любое другое — провал (rc 1)."""
import csv, sys

PAIRS = {"first_lt100": ("first", "lt100"), "rep_ge1000": ("rep", "ge1000")}
a = sys.argv
mine = {(r["hyp"], r["pop"], r["src"], r["month"]): r for r in csv.DictReader(open(a[1], newline=""))}
ref = {(r["touch"], r["strength"], r["src"], r["month"]): r for r in csv.DictReader(open(a[2], newline=""))}
bad = tie = ok = 0
for hid, (t, s) in PAIRS.items():
    for (h, pop, src, mon), r in sorted(mine.items()):
        if h != hid or pop != "event":
            continue
        g = ref.get((t, s, src, mon))
        if g is None:
            print("НЕТ в g015:", h, src, mon); bad += 1; continue
        if int(r["n"]) != int(g["n"]) or int(r["n_days"]) != int(g["n_days"]):
            print("n/n_days:", h, src, mon, r["n"], g["n"], r["n_days"], g["n_days"]); bad += 1; continue
        for c in [k for k in g if k.startswith(("m_", "favour_", "adverse_")) and "-" not in k and not k.startswith("pf")]:
            if g[c] == "":
                bad += r[f"S_{c}"] != "nan"; continue
            v, w = float(r[f"S_{c}"]), float(g[c])
            if round(v, 3) == w:
                ok += 1
            elif abs(v - w) < 1.5e-3:
                tie += 1
            else:
                print("РАСХОЖДЕНИЕ", h, src, mon, c, v, w); bad += 1
    keys = [k for k in mine if k[0] == hid and k[1] == "event"]
    have = {(k[2], k[3]) for k in mine}  # гейт малым срезом: ожидаем только присутствующие в выходе (src, месяц)
    miss = [k for k in ref if k[0] == t and k[1] == s and (k[2], k[3]) in have and (hid, "event", k[2], k[3]) not in mine]
    for k in miss:
        print("НЕТ в pseudotest:", hid, k[2], k[3]); bad += 1
print(f"сошлось {ok}, ничьи округления {tie}, провалов {bad}")
sys.exit(1 if bad else 0)
