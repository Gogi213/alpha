#!/usr/bin/env python3
"""TK-046: списки нового счёта месяца: <MON>-new.txt (сим сутки бинлог — нет D20 деки), <MON>-newcoins.txt (монеты без sigma240 деки).
    python3 tk046-mnew.py jan"""
import csv, os, sys
MON = sys.argv[1]; YM = {"jan": "2026-01", "feb": "2026-02", "aug": "2026-08"}[MON]
OR = f"/data/alpha/epochs/e-{MON}/root"; OS = f"/home/deck/alpha/epochs/e-{MON}/study"; VR = f"/data/tk037/vroots/e-{YM}"
rows = [(r["sym"], r["day"]) for r in csv.DictReader(open("/data/tk044/final3/verdict.csv")) if r["day"].startswith(YM)]
new, coins, nobin = [], set(), []
for s, d in rows:
    if not os.path.exists(f"{OS}/approaches/D20/{d}/approaches-{s}.csv"):
        b = next((p for p in (f"{VR}/{s}-{d}.binlog", f"{OR}/{s}-{d}.binlog") if os.path.exists(p)), None)
        (new.append((s, d, b)) if b else nobin.append((s, d)))
    if not os.path.exists(f"{OS}/sigma240/sigma-{s}.csv"):
        coins.add(s)
os.makedirs(f"/data/tk046/{MON}", exist_ok=True)
open(f"/data/tk046/{MON}-new.txt", "w").write("".join(f"{s} {d} {b}\n" for s, d, b in new))
open(f"/data/tk046/{MON}-newcoins.txt", "w").write("".join(f"{c}\n" for c in sorted(coins)))
print(MON, "строк вердикта", len(rows), "новых", len(new), "без бинлога", len(nobin), "монет без sigma", len(coins))
