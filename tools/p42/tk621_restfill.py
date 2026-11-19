#!/usr/bin/env python3
"""TK-621: fill 1m klines days that data.binance.vision does not publish (tk621_gaps.py) from fapi REST, in the published CSV layout.
  python3 tk621_restfill.py <list_dir> <out_dir>           # fills (kline gaps of symbols not already on disk in the TK-599 trees)
  python3 tk621_restfill.py --selftest SYMBOL DAY <out_dir> # REST-vs-published byte comparison of one published day
Layout = unzipped data.binance.vision file: header + rows `open_time,open,high,low,close,volume,close_time,quote_volume,count,
taker_buy_volume,taker_buy_quote_volume,ignore`. Output <out>/klines-csv/<SYM>/<day>.csv; the list of REST-filled days -> <out>/log/restfill.tsv."""
import datetime as D
import json
import os
import sys
import urllib.parse
import urllib.request

HDR = "open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore\n"


def rest_day(sym, day):
    t0 = int(D.datetime.fromisoformat(day).replace(tzinfo=D.timezone.utc).timestamp() * 1000)
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={urllib.parse.quote(sym)}&interval=1m&startTime={t0}&endTime={t0 + 86_400_000 - 1}&limit=1500"
    rows = json.load(urllib.request.urlopen(url, timeout=60))
    return HDR + "".join(",".join(str(x) for x in r) + "\n" for r in rows), len(rows)


if sys.argv[1] == "--selftest":
    sym, day, out = sys.argv[2:5]
    body, n = rest_day(sym, day)
    pub = open(f"{out}/klines-csv/{sym}/{day}.csv").read()
    print("rows", n, "identical to published:", body == pub)
    sys.exit(0)

L, OUT = sys.argv[1:3]
os.makedirs(OUT + "/log", exist_ok=True)
gaps = [l.rstrip("\n").split("\t") for l in open(L + "/gaps.tsv")]
filled = []
for kind, sym, day in gaps:
    if kind != "klines" or os.path.exists(f"/data/p42-tk599/wa/klines/{sym}/{day}.csv") or any(
            os.path.exists(f"/data/p42-tk599/{w}/klines/{sym}/{day}.csv") for w in ("wb", "wc")):
        continue                                    # the old trees already hold this day (REST-filled earlier by the TK-599 prep)
    body, n = rest_day(sym, day)
    if n:
        os.makedirs(f"{OUT}/klines-csv/{sym}", exist_ok=True)
        open(f"{OUT}/klines-csv/{sym}/{day}.csv", "w").write(body)
    filled.append((sym, day, n))
with open(OUT + "/log/restfill.tsv", "w") as f:
    for r in filled:
        f.write("\t".join(map(str, r)) + "\n")
print(len(filled), filled)
