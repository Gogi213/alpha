#!/usr/bin/env python3
"""TK-366: список монет с оборотом 24 ч > $50k (снимок) и дельта к тому, что уже есть в бинлогах июль–сентябрь.
  tk366-list.py <снимок.csv> <fut-have.txt> <spot-have.txt> <out-каталог>
have: строки "SYM DAY" (спот-файл кончается строкой "==" и списком исключённых монет)."""
import csv, sys, datetime as dt, os
snap, fh, sh, out = sys.argv[1:5]
os.makedirs(out, exist_ok=True)
days = [(dt.date(2026, 7, 1) + dt.timedelta(d)).isoformat() for d in range((dt.date(2026, 9, 30) - dt.date(2026, 7, 1)).days + 1)]
coins = {"fut": [], "spot": []}
with open(snap, encoding="utf-8-sig") as f:
    for r in csv.DictReader(f, delimiter=";"):
        t = float(r["turnover24h_usd"])
        if t > 50000:
            coins["fut" if r["market"].startswith("ф") else "spot"].append((r["symbol"], t))
def have(path):
    s, excl = set(), set()
    seen_eq = False
    for ln in open(path, encoding="utf-8"):
        ln = ln.strip()
        if ln == "==": seen_eq = True; continue
        if seen_eq:
            excl.add(ln.split("-")[0]); continue
        a = ln.split()
        if len(a) == 2: s.add((a[0], a[1]))
    return s, excl
for mk, hp in (("fut", fh), ("spot", sh)):
    h, excl = have(hp)
    have_syms = {a for a, _ in h}
    rows = sorted(coins[mk], key=lambda x: -x[1])
    with open(f"{out}/{mk}-coins.csv", "w") as f:
        f.write("symbol,turnover24h_usd,have_days,todo_days\n")
        todo = []
        for s, t in rows:
            hd = sum((s, d) in h for d in days)
            f.write(f"{s},{t:.0f},{hd},{len(days)-hd}\n")
            todo += [(s, d) for d in days if (s, d) not in h]
    open(f"{out}/{mk}-todo.txt", "w").write("".join(f"{s} {d}\n" for s, d in todo))
    print(mk, "монет", len(rows), "из них есть в бинлогах", sum(1 for s, _ in rows if s in have_syms),
          "| монето-суток всего", len(rows) * len(days), "есть", sum(1 for s, _ in rows for d in days if (s, d) in h), "к закачке (до 404)", len(todo))
