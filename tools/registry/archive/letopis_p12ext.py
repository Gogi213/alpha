#!/usr/bin/env python3
"""Летопись, TK-089: 7 клеток расширения П-12 (В-198) — не Г-NN, а варианты базы B1 (П-10 §4): привязка клетка -> протокол П-12 §расширение,
месяцы из p12-ext-monthly (по данным), вердикт — p12-ext-verdict (Судья принял, fe940e34)."""
import csv, collections, os
F = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "docs", "findings")
mon = collections.defaultdict(set)
for r in csv.DictReader(open(os.path.join(F, "p12-ext-monthly-2026-10-08.csv"), encoding="utf-8")):
    r["month"][5:] and mon[r["cell"]].add(r["month"][5:])
FAM = {"x-stop": "стоп", "x-dl": "дедлайн", "x-entry": "форма входа"}
rows = [{"клетка": c, "что_меняет_в_B1": next(v for k, v in FAM.items() if c.startswith(k)), "протокол": "П-12 (расширение, В-198)",
         "месяцы_в_данных": ",".join(sorted(x for x in m if x)), "вердикт": "не отобрана (§9 п.1, Судья принял, fe940e34; p12-ext-verdict-2026-10-08.md)",
         "привязка_к_Г-NN": "нет: клетка — вариант базы B1, не гипотеза пула"} for c, m in sorted(mon.items()) if c.startswith("x-")]
out = os.path.join(F, "letopis-p12ext-2026-10-08")
with open(out + ".csv", "w", encoding="utf-8", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter=";"); w.writeheader(); w.writerows(rows)
with open(out + ".md", "w", encoding="utf-8") as f:
    f.write("# Летопись: клетки расширения П-12 (TK-089, 08.10)\n\nЭто варианты базы B1, а не гипотезы Г-NN пула: их привязка — к протоколу П-12, а не к Г-NN.\n\n"
            "| " + " | ".join(rows[0]) + " |\n|" + "---|" * len(rows[0]) + "\n" + "\n".join("| " + " | ".join(r.values()) + " |" for r in rows) + "\n")
print(len(rows), "клеток")
