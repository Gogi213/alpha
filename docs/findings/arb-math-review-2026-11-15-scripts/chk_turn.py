"""Turnover column vs exchange REST 24h quote volume (same rolling 24 h, ending ~now). Read-only."""
import json
import sys
import time
import urllib.request

import numpy as np

sys.path.insert(0, "/opt/arb-tab/tools/arb_stats")
import arb_daemon  # noqa: E402
import arb_live  # noqa: E402


def get(u):
    with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "x"}), timeout=30) as f:
        return json.loads(f.read())


T = json.load(open("/data/arb-tab/live/turnover.json"))
now = int(time.time() * 1000)
print("turnover window h", (T["t1"] - T["t0"]) / 3600_000, "lag to now min", (now - T["t1"]) / 60000)
rest = {}
rest["binance"] = {x["symbol"]: float(x["quoteVolume"]) for x in get("https://fapi.binance.com/fapi/v1/ticker/24hr")}
rest["bybit"] = {x["symbol"]: float(x["turnover24h"]) for x in get("https://api.bybit.com/v5/market/tickers?category=linear")["result"]["list"]}
okx = get("https://www.okx.com/api/v5/market/tickers?instType=SWAP")["data"]
rest["okx"] = {x["instId"]: float(x["volCcy24h"]) * (float(x["open24h"]) + float(x["last"])) / 2 for x in okx}
bx = get("https://open-api.bingx.com/openApi/swap/v2/quote/ticker")["data"]
rest["bingx"] = {x["symbol"]: float(x["quoteVolume"]) for x in bx}
try:
    rest["gate"] = {x["contract"]: float(x.get("volume_24h_quote") or 0) for x in get("https://api.gateio.ws/api/v4/futures/usdt/tickers")}
except Exception as e:  # noqa: BLE001
    print("gate rest fail", e)
day = arb_live.days_between(T["t1"], T["t1"])[0]
_, coins = arb_daemon.coin_map(day)
for ex in arb_daemon.EXCHANGES:
    pts = []
    for coin, per in coins.items():
        if ex not in per:
            continue
        sym = per[ex][0]
        ours = (T["coin"].get(coin) or {}).get(ex)
        theirs = rest.get(ex, {}).get(sym)
        if ours is None or not theirs or theirs < 1e6:
            continue
        pts.append((ours / theirs, coin, sym, ours, theirs))
    if not pts:
        print(ex, "no points")
        continue
    r = np.array([p[0] for p in pts])
    print("%-8s n=%d ours/REST p5 %.3f p50 %.3f p95 %.3f; outside 0.8..1.25: %d" % (ex, len(r), *np.percentile(r, [5, 50, 95]), ((r < 0.8) | (r > 1.25)).sum()))
    for p in sorted(pts, key=lambda p: abs(np.log(p[0])), reverse=True)[:6]:
        print("    %s %s ratio %.3f ours %.0f rest %.0f" % (p[1], p[2], p[0], p[3], p[4]))
