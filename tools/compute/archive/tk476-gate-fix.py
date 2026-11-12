#!/usr/bin/env python3
"""TK-476: части Gate, собранные прежним сборщиком из архивов «от новых к старым» (id убывает), имеют o/c наоборот и повторы минут.
Удаляет часть (gate-parts/<C>/<ГГГГММ>.csv.gz) каждого такого месяца и отметку контракта (gate-cov/<C>.tsv + итоговый файл) —
tk476-gate.py соберёт заново новым сборщиком. Направление — id первых двух сделок сырого файла. usage: tk476-gate-fix.py <OUT>"""
import gzip, os, sys

out = sys.argv[1]
fixed, desc = 0, {}
for c in sorted(os.listdir(out + "/gate-raw")):
    for fn in sorted(os.listdir(f"{out}/gate-raw/{c}")):
        if not fn.endswith(".csv.gz"):
            continue
        ym = fn.rsplit("-", 1)[1][:6]
        with gzip.open(f"{out}/gate-raw/{c}/{fn}", "rt") as g:
            ids = [int(next(g).split(",")[1]) for _ in range(2)]
        if ids[1] < ids[0]:
            desc.setdefault(c, []).append(ym)
            for p in (f"{out}/gate-parts/{c}/{ym}.csv.gz",):
                if os.path.exists(p):
                    os.remove(p); fixed += 1
for c in desc:
    for p in (f"{out}/gate-cov/{c}.tsv", f"{out}/gate-futures_usdt/{c}.csv.gz"):
        if os.path.exists(p):
            os.remove(p)
print("контрактов с убывающими месяцами", len(desc), "частей к пересборке", fixed, "месяцев всего", sum(len(v) for v in desc.values()), flush=True)
with open(out + "/gate-desc-months.tsv", "w") as f:
    for c, v in desc.items():
        f.write(c + "\t" + ",".join(v) + "\n")
