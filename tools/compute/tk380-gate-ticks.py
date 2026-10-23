"""TK-380: 24ч-тикеры Gate (спот USDT + фьюч USDT) -> csv в формате tk364-enum.py (market;symbol;price;turnover24h_usd). usage: tk380-gate-ticks.py <spot.json> <fut.json> <out.csv>"""
import sys, json, re, csv
spot = json.load(open(sys.argv[1])); fut = json.load(open(sys.argv[2]))
rows = []
for t in spot:
    m = re.match(r"^(.+)_USDT$", t["currency_pair"])
    if not m or re.search(r"(3L|3S|5L|5S)$", m.group(1)): continue
    rows.append(("спот", m.group(1) + "USDT", t["last"], t["quote_volume"]))
for t in fut:
    m = re.match(r"^(.+)_USDT$", t["contract"])
    if not m: continue
    rows.append(("фьюч", m.group(1) + "USDT", t["last"], t["volume_24h_quote"]))
with open(sys.argv[3], "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f, delimiter=";"); w.writerow(["market", "symbol", "price", "turnover24h_usd"]); w.writerows(rows)
print(len(rows))
