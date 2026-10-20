"""TK-328 §4.4: доля сделок B1 фьюча (t0->exit), пересекающих funding 00/08/16 UTC. Запуск на calc: python3 funding.py"""
import csv, glob
B1F = "ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"
H8 = 8 * 3600 * 10**9
tot = cross = 0
bym = {}
for p in sorted(glob.glob("/data/p12r3a/b1-b/2026-*/*/t-bid-btc4h-q1/rounds.csv")):
    mo = p.split("/")[-4]
    rows = csv.DictReader(l for l in open(p) if not l.startswith("#"))
    for r in rows:
        if r["form"] != B1F or not r["exit_ns"] or int(r["legs_filled"] or 0) == 0:
            continue
        t0 = int(r["t0_ns"]); ex = int(r["exit_ns"])
        c = (t0 // H8) != (ex // H8)
        tot += 1; cross += c
        a = bym.setdefault(mo, [0, 0]); a[0] += 1; a[1] += c
print("all", tot, cross, round(cross / tot, 4))
for mo, (n, c) in sorted(bym.items()):
    print(mo, n, c, round(c / n, 4))
