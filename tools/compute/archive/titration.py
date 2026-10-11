#!/usr/bin/env python3
"""Титрование от полов В-66 по определению владельца (19.09): уровень = номинал >= $10k
в момент касания; сила = плотность / оборот монеты за час (×поток); возраст плотности при
касании. Вход: каталог touches-*.csv с колонками flow_1h_lots/strength_flow_pct, instruments.csv.
Использование: titration.py <каталог touches> <instruments.csv> [минимальный номинал $]"""
import csv, os, sys

d = sys.argv[1]; inst = sys.argv[2]; usd_min = float(sys.argv[3]) if len(sys.argv) > 3 else 10000.0
ticks = {}
for r in csv.DictReader(l for l in open(inst, encoding="utf-8") if not l.startswith("#")):
    try: ticks[r["symbol"]] = float(r["tick_size"])
    except (KeyError, ValueError): pass
rows = []
for name in sorted(os.listdir(d)):
    if not (name.startswith("touches-") and name.endswith(".csv")): continue
    tick = ticks.get(name[8:-4])
    if tick is None: continue
    with open(os.path.join(d, name), encoding="utf-8") as f:
        for r in csv.DictReader(l for l in f if not l.startswith("#")):
            try:
                usd = float(r["size_at_touch"]) * float(r["price_tick"]) * tick
                if usd < usd_min: continue
                fs = float(r["strength_flow_pct"]) if r["strength_flow_pct"] else None
                age = int(r["age_ms"]); surv = r["ended_by_death"] != "true"
                m10 = float(r["m_10m"]) if r["m_10m"] else None
                m1h = float(r["m_1h"]) if r["m_1h"] else None
                rows.append((usd, fs, age, surv, m10, m1h))
            except (KeyError, ValueError): pass
print(f"касаний плотностей с номиналом >= ${usd_min:g} в момент касания: {len(rows)} (двое суток, пул)")
fs = sorted(x[1] for x in rows if x[1] is not None)
if fs:
    q = lambda p: fs[int(p * (len(fs) - 1))]
    print("сила ×поток (плотность / оборот за час), проценты: " + " ".join(f"p{int(p*100)}={q(p):.2f}" for p in (.1, .25, .5, .75, .9, .99)))
print()
print("касаний в сутки на пул / доля выживших / markout 10 мин / 1 ч (bps, в сторону отскока)")
ages = [("любой", 0), (">=1мин", 60000), (">=10мин", 600000), (">=1ч", 3600000)]
print(f"{'сила \\ возраст':14s}" + "".join(f"{a:30s}" for a, _ in ages))
for lab, smin in [("любая", 0), (">=1%", 1), (">=5%", 5), (">=10%", 10), (">=25%", 25), (">=50%", 50), (">=100%", 100)]:
    line = f"{lab:14s}"
    for _, amin in ages:
        sel = [x for x in rows if (x[1] or 0) >= smin and x[2] >= amin]
        n = len(sel)
        if n == 0:
            line += f"{'0':30s}"; continue
        sv = sum(1 for x in sel if x[3]) / n
        m10 = [x[4] for x in sel if x[4] is not None]; m1h = [x[5] for x in sel if x[5] is not None]
        a10 = sum(m10) / len(m10) if m10 else float("nan"); a1h = sum(m1h) / len(m1h) if m1h else float("nan")
        cell = f"{n/2:7.0f}/д {sv:4.0%} {a10:+5.1f} {a1h:+6.1f}"
        line += f"{cell:30s}"
    print(line)
print()
print("Чтение: строка — минимальная сила ×поток, столбец — минимальный возраст плотности при касании; ячейка — касаний в сутки на весь пул, доля выживших уровней, средний markout 10 мин / 1 ч.")
