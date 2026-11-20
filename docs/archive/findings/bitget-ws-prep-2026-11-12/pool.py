#!/usr/bin/env python3
"""Пул Bitget по правилу G-022: USDT-FUTURES, symbolType=perpetual, symbolStatus=normal, quoteCoin=USDT, оборот за 24 ч ≥ порога $, топ-10 не исключается.
Оборот — `usdtVolume` из REST /api/v2/mix/market/tickers (готовое поле в USDT, второго запроса не нужно).
  pool.py --out pool-DAY.json [--min 300000]"""
import argparse, json, time, urllib.request

REST = "https://api.bitget.com/api/v2/mix/market"


def get(path):
    for k in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(REST + path, headers={"User-Agent": "alpha-collector/bitget-pool"}), timeout=30) as r:
                d = json.load(r)
                assert d["code"] == "00000", d
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
    ins = [i for i in get("/contracts?productType=USDT-FUTURES") if i["symbolType"] == "perpetual" and i["symbolStatus"] == "normal" and i["quoteCoin"] == "USDT"]
    tk = {t["symbol"]: t for t in get("/tickers?productType=USDT-FUTURES")}
    qv = {i["symbol"]: float(tk[i["symbol"]]["usdtVolume"]) for i in ins if i["symbol"] in tk}
    out = {"ts_ms": int(t0 * 1000), "source": "bitget REST /api/v2/mix/market/contracts + /tickers (USDT-FUTURES): symbolType=perpetual, symbolStatus=normal, quoteCoin=USDT; turnover=usdtVolume",
           "min_quote_volume": a.min, "top_excluded": [], "n_usdt_perp_normal": len(ins), "pool": sorted(k for k, v in qv.items() if v >= a.min),
           "meta": {i["symbol"]: {k: i[k] for k in ("baseCoin", "minTradeNum", "pricePlace", "volumePlace", "sizeMultiplier", "priceEndStep", "fundInterval", "isRwa", "launchTime", "maxLever")} for i in ins if qv.get(i["symbol"], 0) >= a.min},
           "qv": {k: round(qv[k], 2) for k in sorted(qv, key=lambda x: -qv[x]) if qv[k] >= a.min}}
    json.dump(out, open(a.out, "w"))
    print("perp_normal", len(ins), "pool", len(out["pool"]))


if __name__ == "__main__":
    main()
