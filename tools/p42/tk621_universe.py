#!/usr/bin/env python3
"""TK-621: contract list "as it was" -> contracts.csv + symbol lists (comma-separated, sorted, as /data/p42-t566/symbols.txt).
  python3 tk621_universe.py <list_dir> <out_dir>
Universe = every USDT-M perpetual with published aggTrades on any day 2026-07-01..2026-11-13 (delisted ones included; window warm-up
days 06-17..06-30 are downloaded too, they are not a membership criterion). TradFi = exchangeInfo contractType TRADIFI_PERPETUAL
(contracts no longer in exchangeInfo: flag from the name table below -> none needed in this window, see `tradfi_src`)."""
import csv
import datetime as D
import json
import sys

L, OUT = sys.argv[1], sys.argv[2]
C = json.load(open(L + "/contracts.json"))["contracts"]
s566 = set(open("/data/p42-t566/symbols.txt").read().replace("\n", ",").split(",")) - {""}
day = lambda ms: D.datetime.fromtimestamp(ms / 1000, D.timezone.utc).strftime("%Y-%m-%d") if ms else ""
rows = []
for s, c in sorted(C.items()):
    if c["last_day"] < "2026-07-01":
        continue  # no trading day inside 07-01..11-13
    tradfi = c["contract_type"] == "TRADIFI_PERPETUAL"
    delisted = c["last_day"] < "2026-11-13"
    rows.append({
        "symbol": s, "in_566": int(s in s566), "tradfi": int(tradfi),
        "tradfi_src": "exchangeInfo" if c["in_exchange_info"] else "not in exchangeInfo (delisted, treated as crypto: COIN name)",
        "contract_type": c["contract_type"] or "", "underlying_type": c["underlying_type"] or "",
        "underlying_sub_type": "|".join(c["underlying_sub_type"] or []), "status": c["status"] or "",
        "onboard_date": day(c["onboard_ms"]), "first_agg_day": c["first_day"], "last_agg_day": c["last_day"],
        "delisted_in_window": int(delisted), "agg_days": c["n_agg_days"], "kl_days": c["n_kl_days"],
        "agg_gb": round(c["agg_bytes"] / 1e9, 3),
    })
with open(OUT + "/contracts.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
for name, sel in (("all", lambda r: True), ("crypto", lambda r: not r["tradfi"]), ("tradfi", lambda r: r["tradfi"])):
    syms = sorted(r["symbol"] for r in rows if sel(r))
    open(f"{OUT}/symbols-{name}.txt", "w").write(",".join(syms) + "\n")
    print(name, len(syms))
new = [r for r in rows if not r["in_566"]]
print("contracts", len(rows), "in 566", len(rows) - len(new), "new", len(new))
print("tradfi", sum(r["tradfi"] for r in rows), "of which in 566", sum(r["tradfi"] and r["in_566"] for r in rows))
print("delisted in window", sum(r["delisted_in_window"] for r in rows))
