#!/usr/bin/env python3
"""Пул BingX по правилу G-022: USDT-M бессрочные (swap v2, currency=USDT, status=1 «online»), оборот за 24 ч ≥ порога $, топ-10 не исключается.
Оборот — `quoteVolume` из REST /openApi/swap/v2/quote/ticker (документация: тикер за 24 ч; значения в котируемой валюте USDT). Метаданные — /openApi/swap/v2/quote/contracts.
  pool.py --out pool-DAY.json [--min 300000]   (хост open-api.bingx.com)"""
import argparse, json, time, urllib.request

HOST = "https://open-api.bingx.com/openApi/swap/v2/quote"


def get(path):
    for k in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(HOST + path, headers={"User-Agent": "alpha-collector/bingx-pool"}), timeout=30) as r:
                d = json.load(r)
                assert d["code"] == 0, d
                return d["data"]
        except Exception:
            time.sleep(1 + k)
    raise RuntimeError(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--min", type=float, default=300_000.0)
    a = ap.parse_args()
    t0 = time.time()
    allc = get("/contracts")
    ins = [i for i in allc if i["currency"] == "USDT" and i["status"] == 1]
    tk = {t["symbol"]: t for t in get("/ticker")}
    qv = {i["symbol"]: float(tk[i["symbol"]]["quoteVolume"]) for i in ins if i["symbol"] in tk}
    out = {"ts_ms": int(t0 * 1000), "source": "bingx REST /openApi/swap/v2/quote/contracts + /quote/ticker: currency=USDT, status=1 (online); turnover=quoteVolume (24 ч, USDT)",
           "min_quote_volume": a.min, "top_excluded": [], "n_contracts_all": len(allc), "n_usdt_online": len(ins), "n_usdt_online_with_ticker": len(qv), "pool": sorted(k for k, v in qv.items() if v >= a.min),
           "meta": {i["symbol"]: {k: i.get(k) for k in ("size", "quantityPrecision", "pricePrecision", "tradeMinQuantity", "tradeMinUSDT", "apiStateOpen", "apiStateClose", "launchTime", "displayName", "asset")} for i in ins if qv.get(i["symbol"], 0) >= a.min},
           "qv": {k: round(qv[k], 2) for k in sorted(qv, key=lambda x: -qv[x]) if qv[k] >= a.min}}
    json.dump(out, open(a.out, "w"))
    print("contracts", len(allc), "usdt_online", len(ins), "with_ticker", len(qv), "pool", len(out["pool"]))


if __name__ == "__main__":
    main()
