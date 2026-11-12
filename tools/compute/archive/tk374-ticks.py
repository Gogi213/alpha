"""TK-374: вход для tk364-enum.py — монеты Gate без Bybit (454) с ценой Gate. usage: tk374-ticks.py <universe.csv> <out.csv>"""
import sys, csv, json, urllib.request
def g(u): return json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "x"}), timeout=60))
spot = {t["currency_pair"]: float(t["last"] or 0) for t in g("https://api.gateio.ws/api/v4/spot/tickers")}
fut = {t["contract"]: float(t["last"] or 0) for t in g("https://api.gateio.ws/api/v4/futures/usdt/tickers")}
n = 0; miss = 0
with open(sys.argv[2], "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f, delimiter=";"); w.writerow(["symbol", "market", "turnover24h_usd", "price"])
    for r in csv.DictReader(open(sys.argv[1], encoding="utf-8-sig")):
        px = (fut if r["market"] == "фьюч" else spot).get(r["symbol"], 0)
        if px <= 0: miss += 1; continue
        w.writerow([r["symbol"].replace("_", ""), r["market"], r["turnover24h_usd"], px]); n += 1
print("rows", n, "no price", miss)
