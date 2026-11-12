#!/usr/bin/env python3
"""TK-366 (G-016), неделя 15–21.10.2026: списки «SYM DAY» для tk366-dl.sh.  tk366w-gen.py <fut-coins.csv> <spot-coins.csv> <instruments.csv> <D>
Все монеты снимка (оборот > $50k); фьюч — без суток до листинга (launch_day); спот — отсев по 404 при закачке."""
import csv, sys, datetime as dt
fc, sc, ins, D = sys.argv[1:5]
days = [(dt.date(2026, 10, 15) + dt.timedelta(i)).isoformat() for i in range(7)]
launch = {r["symbol"]: r["launch_day"] for r in csv.DictReader(open(ins, encoding="utf-8"))}
for mk, p in (("fut", fc), ("spot", sc)):
    n = 0
    with open(f"{D}/{mk}-todo.txt", "w") as f:
        for r in csv.DictReader(open(p, encoding="utf-8")):
            s = r["symbol"]
            for d in days:
                if mk == "fut" and launch.get(s, "") > d: continue
                f.write(f"{s} {d}\n"); n += 1
    print(mk, n)
