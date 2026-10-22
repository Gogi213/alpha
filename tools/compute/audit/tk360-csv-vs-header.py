#!/usr/bin/env python3
"""TK-360: instruments.csv спота против заголовка бинлога (тик, шаг лота). Движок берёт тик/шаг из заголовка (read_tick_step),
а лот заявки (PoolLot: min_order_qty/qty_step/min_notional) — из instruments.csv корня.
  tk360-csv-vs-header.py <вывод.csv>   читает /data/tk0319/e-<мес>/root (бинлоги-симлинки + instruments.csv), выборка: каждые 7-е сутки."""
import csv, glob, os, struct, sys
from decimal import Decimal
out = sys.argv[1]
rows = []
for mo in "jan feb mar apr may jun jul aug sep".split():
    root = f"/data/tk0319/e-{mo}/root"
    ins = {}
    with open(f"{root}/instruments.csv", newline="") as f:
        for r in csv.DictReader(l for l in f if not l.startswith("#")):
            ins[r["symbol"]] = r
    by = {}
    for p in sorted(glob.glob(f"{root}/*.binlog")):
        b = os.path.basename(p)[:-7]; sym, day = b.rsplit("-", 3)[0], b[-10:]
        by.setdefault(sym, []).append((day, p))
    for sym, lst in sorted(by.items()):
        hs = set()
        for day, p in lst[::7] + lst[-1:]:
            with open(p, "rb") as f: h = f.read(21)
            t9, s9 = struct.unpack("<qq", h[5:21]); hs.add((t9, s9))
        r = ins.get(sym)
        if r is None:
            rows.append((mo, sym, "", "", "", "", ";".join(f"{a}/{b}" for a, b in sorted(hs)), "нет в csv")); continue
        ct = int(Decimal(r["tick_size"]) * 10**9); cs = int(Decimal(r["qty_step"]) * 10**9)
        ok = len(hs) == 1 and (ct, cs) in hs
        rows.append((mo, sym, r["tick_size"], r["qty_step"], r["min_order_qty"], r["min_notional_value"],
                     ";".join(f"{a}/{b}" for a, b in sorted(hs)), "ok" if ok else ("tick" if all(a != ct for a, _ in hs) else "") + ("step" if all(b != cs for _, b in hs) else "") or "смесь"))
with open(out, "w", newline="") as f:
    w = csv.writer(f); w.writerow("mo sym csv_tick csv_step csv_minqty csv_minnot hdr_tick9/step9 verdict".split()); w.writerows(rows)
from collections import Counter
print(Counter((r[0], r[7]) for r in rows))
