#!/usr/bin/env python3
"""TK-120: ключи сделок B1 R2 (v171c, янв–сен): closes портфеля -> (символ, t0) по строкам rounds (как p12-coin-c2.py: exit_ms + $).
Запуск: p12-r2b-b1keys.py <cap 0|3> <выход.csv>; вход data/p12r2-v171c-a/closes-vn-cap<cap>-<мес>.json, data/tk063/coin-rows.csv."""
import csv, json, sys
from collections import defaultdict
CAP, OUT = sys.argv[1], sys.argv[2]
D = "data/p12r2-v171c-a/"
DROP = {"TRUMPUSDT", "TRXUSDT", "BCHUSDT"}
PRE = "t-bid-btc4h-q1@ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"
rows = defaultdict(list)
for r in csv.DictReader(open("data/tk063/coin-rows.csv", encoding="utf-8")):
    if r["form"] == "B1" and r["symbol"] not in DROP:
        rows[r["month"]].append((int(r["exit_ms"]), float(r["qty"]) * float(r["entry_vwap"]) * float(r["net_bps"]) / 1e4, r["symbol"], int(r["t0_ms"])))
out = []
for m in range(1, 10):
    mo = f"2026-{m:02d}"
    j = json.load(open(f"{D}closes-vn-cap{CAP}-{mo}.json"))
    pool = defaultdict(list)
    for ex, usd, sym, t0 in rows[mo]:
        pool[ex].append((usd, sym, t0))
    for per in j[PRE].values():
        for caps in per.values():
            for a, b in caps:
                ms, v = int(a), float(b)
                c = pool.get(ms, [])
                hit = min(range(len(c)), key=lambda i: abs(c[i][0] - v)) if c else None
                if hit is None or abs(c[hit][0] - v) > 6e-4:
                    raise SystemExit(f"нет пары {mo} {ms} {v}")
                usd, sym, t0 = c.pop(hit)
                out.append((mo, sym, t0, ms))
with open(OUT, "w", encoding="utf-8", newline="") as fh:
    w = csv.writer(fh, lineterminator="\n"); w.writerow(["month", "symbol", "t0_ms", "exit_ms"]); w.writerows(out)
print(len(out))
