#!/usr/bin/env python3
"""TK-355 G-015: сводка потолков из g015-cells.csv -> таблица ручка x потолок x издержки (месяцы отдельно).
  g015-table.py <g015-cells.csv> [touch]   touch = first|rep (по умолчанию first); взвешено по n внутри (src, month)."""
import csv, sys, collections
p = sys.argv[1]; tch = sys.argv[2] if len(sys.argv) > 2 else "first"
rows = [r for r in csv.DictReader(open(p)) if r["touch"] == tch]
MON = "jan feb mar apr may jun jul aug sep".split()
g = collections.defaultdict(list)
for r in rows:
    g[(r["src"], r["month"])].append(r)
def wavg(rs, c):
    num = den = 0.0
    for r in rs:
        if r[c] != "":
            num += float(r[c]) * float(r["n"]); den += float(r["n"])
    return num / den if den else float("nan")
def tbl(title, cols):
    print(f"\n### {title} (touch={tch}, взвешено по n; бп; издержки круга 11,25–13,5)")
    for src in ("fut", "spot"):
        print(f"\n{src}: " + " | ".join(["мес", "n", "дн"] + cols))
        for m in MON:
            rs = g.get((src, m))
            if not rs: continue
            n = sum(int(r["n"]) for r in rs); d = max(int(r["n_days"]) for r in rs)
            print(" | ".join([m, str(n), str(d)] + [f"{wavg(rs, c):.2f}" for c in cols]))
tbl("S: сигнал m_h", ["m_100ms", "m_1s", "m_10s", "m_60s", "m_10m", "m_1h", "m_2h"])
tbl("L: затухание при входе через d (m_h - m_d)", ["m_1s-m_100ms", "m_10s-m_1s", "m_60s-m_10s", "m_10m-m_60s", "m_1h-m_10m"])
tbl("X: оракул-выход favour_H / цена стопа adverse_H", ["favour_60s_bps", "favour_600s_bps", "favour_3600s_bps", "adverse_600s_bps", "adverse_3600s_bps"])
tbl("X: доля касаний с favour_H >= 11,25", ["pfavour_60s_bps>=11.25", "pfavour_600s_bps>=11.25", "pfavour_3600s_bps>=11.25"])
tbl("O: оракул-отбор top-k% по m_10m / m_1h", ["top5_m_10m", "top10_m_10m", "top25_m_10m", "top50_m_10m", "top5_m_1h", "top25_m_1h", "top50_m_1h"])
