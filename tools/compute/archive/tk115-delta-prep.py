#!/usr/bin/env python3
"""TK-115 дельта (В-213): символо-сутки с сигналами B1 по готовым signals (янв — m-jan TK-115, фев–сен — TK-064),
наличие файла D20, цена в ядро-с, сутки-проба янв с макс. строк популяции e106. Выход: /data/tk0115/delta/."""
import os, csv, sys, collections
OUT = "/data/tk0115/delta"; os.makedirs(OUT, exist_ok=True)
MONS = "jan feb mar apr may jun jul aug sep".split()
SIG = lambda m, d: ("/data/tk0115/pool/m-jan" if m == "jan" else f"/data/tk064/pool/m-{m}") + f"/signals/{d}/t-bid-btc4h-q1/signals.csv"
D20 = lambda m, d, s: f"/data/tk046/{m}/home/alpha/epochs/e-{m}/study/approaches/D20/{d}/approaches-{s}.csv"
days = {}
for m in MONS:
    base = f"/data/tk046/{m}/home/alpha/epochs/e-{m}/study/approaches/D20"
    days[m] = sorted(os.listdir(base))
rows = []; miss_sig = []; miss_d20 = []; cnt = collections.Counter(); alld = collections.Counter()
for m in MONS:
    for d in days[m]:
        p = SIG(m, d)
        if not os.path.exists(p): miss_sig.append((m, d)); continue
        syms = set()
        with open(p, encoding="utf-8") as f:
            for r in csv.reader(l for l in f if not l.startswith("#")):
                if r and r[0] != "symbol": syms.add(r[0])
        alld[m] += 1
        for s in sorted(syms):
            rows.append((m, d, s)); cnt[m] += 1
            if not os.path.exists(D20(m, d, s)): miss_d20.append((m, d, s))
with open(f"{OUT}/b1-symdays.csv", "w") as f:
    f.write("month,day,symbol\n"); [f.write(",".join(r) + "\n") for r in rows]
# проба: янв-сутки с макс. числом строк популяции e106 в m-jan/r3c
best = (0, None)
for d in days["jan"]:
    p = f"/data/tk0115/pool/m-jan/r3c/{d}/e106-pop/signals.csv"
    if os.path.exists(p):
        n = sum(1 for l in open(p, encoding="utf-8") if not l.startswith("#")) - 1
        if n > best[0]: best = (n, d)
n = len(rows)
print(f"символо-суток B1: {n} (из ~24863 пула); по месяцам: {dict(cnt)}")
print(f"суток с signals: {dict(alld)}; суток без signals: {miss_sig}")
print(f"нет файла D20: {len(miss_d20)} {miss_d20[:10]}")
print(f"проба: янв {best[1]} строк популяции e106 = {best[0]}")
# цена: touches ~15.7 ядро-с, grid B1 ~3.9, e106 ~1.4 на символо-сутки (m-jan units: 1416/350/130 на 90 символов)
for name, c in (("touches --wall-log (e65)", 15.7), ("журнал B1 grid (e112/e116/e133)", 3.9), ("e106 nostop×3", 1.4)):
    print(f"{name}: {n*c/3600:.1f} ядро-ч (≈ {n*c/16/3600:.2f} ч на 16 потоках)")
open(f"{OUT}/probe-day.txt", "w").write(f"{best[1]}\n")
