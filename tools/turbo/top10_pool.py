#!/usr/bin/env python3
"""G-022 pool-fix: пул Binance / Bybit для лестницы «топ-10 включён» — то же правило, что `select_pool` коллектора (оборот за сутки >= порога, без исключения топа), с оборотом `qv`.

  top10_pool.py <binance|bybit> <out.json> [--min-qv 300000]

Файл: {"pool": [имена по алфавиту], "qv": {имя: оборот $}, "top10": [10 крупнейших по обороту], "low": [до 8 символов чуть ниже порога]} — `steppools.py` режет его на pool-10.json (= ровно прежний исключённый топ-10) и pool-full.json.
Только публичный REST; на ПК владельца не запускать (srv).
"""
import argparse
import json
import urllib.request


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "alpha-collector/top10-pool"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def binance():
    info = get("https://fapi.binance.com/fapi/v1/exchangeInfo")
    qv = {t["symbol"]: float(t["quoteVolume"]) for t in get("https://fapi.binance.com/fapi/v1/ticker/24hr")}
    ok = [s["symbol"] for s in info["symbols"] if s.get("status") == "TRADING" and s.get("contractType") == "PERPETUAL" and s.get("quoteAsset") == "USDT"]
    return {s: qv[s] for s in ok if s in qv}


def bybit():
    ok, cursor = [], ""
    while True:
        page = get("https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1000" + (f"&cursor={cursor}" if cursor else ""))["result"]
        ok += [s["symbol"] for s in page["list"] if s.get("status") == "Trading" and s.get("contractType") == "LinearPerpetual" and s.get("quoteCoin") == "USDT"]
        cursor = page.get("nextPageCursor") or ""
        if not cursor:
            break
    qv = {t["symbol"]: float(t["turnover24h"]) for t in get("https://api.bybit.com/v5/market/tickers?category=linear")["result"]["list"]}
    return {s: qv[s] for s in ok if s in qv}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("exchange", choices=["binance", "bybit"])
    ap.add_argument("out")
    ap.add_argument("--min-qv", type=float, default=300_000.0)
    a = ap.parse_args()
    qv = binance() if a.exchange == "binance" else bybit()
    ranked = sorted(qv, key=lambda s: (-qv[s], s))
    pool = sorted(s for s in qv if qv[s] >= a.min_qv)
    low = [s for s in ranked if 0 < qv[s] < a.min_qv][:8]   # под порогом, но торгуются: для проверки снятия по гистерезису (poolsmoke.sh)
    json.dump({"pool": pool, "qv": {s: qv[s] for s in pool}, "top10": ranked[:10], "low": low}, open(a.out, "w"))
    print(a.exchange, "pool", len(pool), "top10", ranked[:10])


if __name__ == "__main__":
    main()
