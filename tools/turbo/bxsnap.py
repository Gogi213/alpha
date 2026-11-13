#!/usr/bin/env python3
"""G-022 BingX: REST snapshot of recent trades, taken right after a collection window (the exchange's own record, the oracle for `restcheck.py bingx`).

BingX REST has no trade history: /openApi/swap/v2/quote/trades returns only the LAST 1000 trades of a symbol (no time or id parameters; /v1/market/historicalTrades
ignores fromId without an API key). A deferred REST pass (RESTPASS=defer) cannot see a window of a busy symbol hours later, so the snapshot is taken at the window end:
every symbol of the step pool, busiest first (their 1000 trades span the shortest time), paced under the exchange limit (500 requests / 10 s).
  bxsnap.py <pool.json> <t0_ms> <t1_ms> <out.json.gz> [--rate 40]
Output: {"fetched_ms", "symbols": {sym: {"at": ms, "n": trades returned, "oldest": ms, "newest": ms, "trades": [[id, price, qty, buyer_is_maker, time_ms] for t0 <= time <= t1]}}, "errors": {sym: text}}.
"""
import argparse
import concurrent.futures
import gzip
import json
import threading
import time
import urllib.request

URL = "https://open-api.bingx.com/openApi/swap/v2/quote/trades?symbol={sym}&limit=1000"
UA = {"User-Agent": "alpha-collector/bingx-check"}
_LOCK = threading.Lock()
_NEXT = [0.0]


def pace(gap):
    with _LOCK:
        now = time.monotonic()
        at = max(now, _NEXT[0])
        _NEXT[0] = at + gap
    if at > now:
        time.sleep(at - now)


def fetch(sym, gap, retries=4):
    for i in range(retries):
        try:
            pace(gap)
            with urllib.request.urlopen(urllib.request.Request(URL.format(sym=sym), headers=UA), timeout=20) as r:
                j = json.load(r)
            if j.get("code") != 0:
                raise RuntimeError(f"code {j.get('code')} {j.get('msg')}")
            return int(time.time() * 1000), j["data"]
        except Exception as e:  # noqa: BLE001
            if i == retries - 1:
                return None, str(e)
            time.sleep(1.0 * (i + 1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pool")
    ap.add_argument("t0", type=int)
    ap.add_argument("t1", type=int)
    ap.add_argument("out")
    ap.add_argument("--rate", type=float, default=40.0, help="requests per second over all threads (exchange limit 50/s)")
    a = ap.parse_args()
    d = json.load(open(a.pool))
    qv = d.get("qv") or {}
    syms = sorted(d["pool"], key=lambda s: -qv.get(s, 0.0))
    out, errors = {}, {}

    def one(sym):
        at, data = fetch(sym, 1.0 / a.rate)
        if at is None:
            errors[sym] = data
            return
        tr = [[int(x["fillId"]), x["price"], x["qty"], bool(x["isBuyerMaker"]), int(x["time"])] for x in data]
        times = [x[4] for x in tr]
        out[sym] = {"at": at, "n": len(tr), "oldest": min(times, default=None), "newest": max(times, default=None), "trades": [x for x in tr if a.t0 <= x[4] <= a.t1]}

    t = time.time()
    with concurrent.futures.ThreadPoolExecutor(16) as ex:
        list(ex.map(one, syms))
    doc = {"fetched_ms": int(t * 1000), "took_s": round(time.time() - t, 1), "symbols": out, "errors": errors}
    with gzip.open(a.out, "wt") as f:
        json.dump(doc, f)
    print(json.dumps({"symbols": len(out), "errors": len(errors), "took_s": doc["took_s"]}))


if __name__ == "__main__":
    main()
