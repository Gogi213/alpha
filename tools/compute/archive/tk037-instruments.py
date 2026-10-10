#!/usr/bin/env python3
"""TK-037: instruments.csv для импорта архива по всем монетам ever-top100 (шаги из /v5/market/instruments-info).
  tk037-instruments.py <ever-top100.csv> <out instruments.csv>   Снятые с торгов — в missing.txt рядом."""
import csv, json, sys, urllib.request
src, out = sys.argv[1], sys.argv[2]
want = [r["symbol"] for r in csv.DictReader(open(src))]
info = {}
for st in ("Trading", "Closed", "Settling", "PreLaunch"):
    cur = ""
    while True:
        u = f"https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1000&status={st}" + (f"&cursor={cur}" if cur else "")
        j = json.loads(urllib.request.urlopen(u, timeout=30).read())
        if j["retCode"] != 0: break
        for i in j["result"]["list"]:
            info.setdefault(i["symbol"], i)
        cur = j["result"].get("nextPageCursor", "")
        if not cur: break
miss = [s for s in want if s not in info]
with open(out, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow("symbol,tick_size,min_order_qty,qty_step,min_notional_value,h3_lots,k,median_trade_lots,window_start_utc_ms,window_secs,depth_check".split(","))
    for s in want:
        if s in info:
            i = info[s]
            w.writerow([s, i["priceFilter"]["tickSize"], i["lotSizeFilter"]["minOrderQty"], i["lotSizeFilter"]["qtyStep"], i["lotSizeFilter"].get("minNotionalValue", ""), "", "", "", "", "", ""])
open(out + ".missing.txt", "w").write("\n".join(miss) + "\n")
print(len(want), "wanted", len(want) - len(miss), "with steps", len(miss), "without")
