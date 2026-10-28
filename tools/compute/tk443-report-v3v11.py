#!/usr/bin/env python3
"""TK-443: сводка V3/V11 из помесячных csv (tk443-v3v11.py) -> md-таблицы D=360 + общий csv. python -I tk443-report-v3v11.py <каталог csv> <вых.csv>"""
import csv, glob, sys, statistics as st
d, out = sys.argv[1], sys.argv[2]
rows = []
for f in sorted(glob.glob(f"{d}/*.csv")):
    rows += list(csv.DictReader(open(f, encoding="utf-8")))
with open(out, "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()), lineterminator="\n"); w.writeheader(); w.writerows(rows)
by = {}
for r in rows:
    if r["D"] == "360" and r["направление"] in ("up", "dn"):
        by.setdefault((r["сигнал"], r["направление"]), {})[r["месяц"]] = r
def f(x): return float(x) if x not in ("", None) else float("nan")
for dr, nm in (("up", "Вверх"), ("dn", "Вниз")):
    print(f"\n{nm} (D=360):\n| сигнал | сент. точн. | подъём [CI] | полнота | срабат/сут/монету | авг. подъём | янв–июль подъём (мин…мед…макс) |\n|---|---|---|---|---|---|---|")
    for (s, k), m in by.items():
        if k != dr: continue
        a, g = m["2026-09"], m.get("2026-08")
        js = [f(m[x]["подъём"]) for x in m if x not in ("2026-09", "2026-08")]
        js = [x for x in js if x == x]
        print(f"| {s} | {f(a['точность']):.3f} | {f(a['подъём']):.1f} [{f(a['подъём_lo']):.1f}; {f(a['подъём_hi']):.1f}] | {f(a['полнота']):.2f} | {f(a['срабат_в_сутки_на_монету']):.2f} | {f(g['подъём']):.1f} | {min(js):.1f}…{st.median(js):.1f}…{max(js):.1f} |")
