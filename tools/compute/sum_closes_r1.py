#!/usr/bin/env python3
"""TK-251: сводка closes-cap{0,3}-<мес>.json (R1X / R1COL, psim p12-r1-psim.py: ключ = хеш клетки, B1 — база) -> csv variant,month,cap,n,sum_usd.
Без пересчёта (В-213). Месяц в имени файла jan…sep, внутри ключа — 2026-MM. Итоги по (месяц, cap) печатает в stdout для сверки с Летописью.
  sum_closes_r1.py <каталог psim> <out.csv>"""
import json, sys
d, out = sys.argv[1], sys.argv[2]
MON = "jan feb mar apr may jun jul aug sep".split()
tot = {}
with open(out, "w", encoding="utf-8", newline="\n") as f:
    f.write("variant,month,cap,n,sum_usd\n")
    for mon in MON:
        for cap in (0, 3):
            for v, per in json.load(open(f"{d}/closes-cap{cap}-{mon}.json")).items():
                if v == "B1":
                    continue
                for mo, caps in per.items():
                    for c, tr in caps.items():
                        s = sum(x[1] for x in tr)
                        f.write(f"{v},{mo},{c},{len(tr)},{s:.4f}\n")
                        t = tot.setdefault((mo, c), [0, 0.0, 0]); t[0] += len(tr); t[1] += s; t[2] += 1
for (mo, c), (n, s, k) in sorted(tot.items()):
    print(f"TOT {mo} cap{c} хешей={k} n={n} sum_usd={s:.4f}")
