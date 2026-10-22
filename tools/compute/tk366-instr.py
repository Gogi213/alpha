#!/usr/bin/env python3
"""TK-366: instruments.csv (symbol,tick_size,qty_step,launch_day) для фьючерса из публичного REST Bybit (linear) и
fut-todo-launch.txt = fut-todo.txt без суток до листинга (дельта к 404). Запуск: tk366-instr.py <data/tk366>"""
import json, sys, urllib.request, datetime as dt
d = sys.argv[1]; rows, cur = {}, ""
while True:
    u = "https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1000" + (f"&cursor={cur}" if cur else "")
    r = json.load(urllib.request.urlopen(u, timeout=30))["result"]
    for x in r["list"]:
        rows[x["symbol"]] = (x["priceFilter"]["tickSize"], x["lotSizeFilter"]["qtyStep"],
                             dt.datetime.fromtimestamp(int(x["launchTime"]) / 1000, dt.timezone.utc).date().isoformat())
    cur = r.get("nextPageCursor") or ""
    if not cur: break
coins = [l.split(",")[0] for l in open(f"{d}/fut-coins.csv").read().splitlines()[1:]]
miss = [c for c in coins if c not in rows]
with open(f"{d}/instruments.csv", "w") as f:
    f.write("symbol,tick_size,qty_step,launch_day\n")
    for c in coins:
        if c in rows: f.write(f"{c},{rows[c][0]},{rows[c][1]},{rows[c][2]}\n")
todo = [l.split() for l in open(f"{d}/fut-todo.txt")]
keep = [(s, day) for s, day in todo if s in rows and day >= rows[s][2]]
open(f"{d}/fut-todo-launch.txt", "w").write("".join(f"{s} {day}\n" for s, day in keep))
print("монет без записи в REST (нет на бирже сейчас):", miss, "| todo", len(todo), "→ после листинга", len(keep))
