#!/usr/bin/env python3
"""Пул OKX по правилу G-022: USDT-SWAP (линейные бессрочные, state=live), оборот ≥ порога $/сутки, без исключения топ-10 (правило топ-10 — только Binance/Bybit).
Оборот = volCcy24h (база, из REST tickers SWAP) × last — единственный запрос; сверка с суммой volCcyQuote 24 часовых свечей (--check-candles: 1 запрос на символ).
  pool.py --out pool-DAY.json [--min 300000] [--check-candles]  (REST https://www.okx.com/api/v5/..., заголовок User-Agent обязателен — без него Cloudflare отдаёт 403)"""
import argparse, concurrent.futures as cf, json, time, urllib.request

REST = "https://www.okx.com/api/v5"


def get(path):
    for k in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(REST + path, headers={"User-Agent": "alpha-collector/okx-pool"}), timeout=30) as r:
                return json.load(r)["data"]
        except Exception:
            time.sleep(1 + k)
    raise RuntimeError(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--min", type=float, default=300_000.0)
    ap.add_argument("--check-candles", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    ins = [i for i in get("/public/instruments?instType=SWAP") if i["ctType"] == "linear" and i["settleCcy"] == "USDT" and i["state"] == "live"]
    tk = {t["instId"]: t for t in get("/market/tickers?instType=SWAP")}
    qv = {i["instId"]: float(tk[i["instId"]]["volCcy24h"]) * float(tk[i["instId"]]["last"]) for i in ins if i["instId"] in tk}
    out = {"ts_ms": int(t0 * 1000), "source": "okx REST /public/instruments + /market/tickers (instType=SWAP): ctType=linear, settleCcy=USDT, state=live; turnover=volCcy24h*last",
           "min_quote_volume": a.min, "top_excluded": [], "n_usdt_swap_live": len(ins), "pool": sorted(k for k, v in qv.items() if v >= a.min),
           "meta": {i["instId"]: {k: i[k] for k in ("ctVal", "ctMult", "ctValCcy", "lotSz", "tickSz", "minSz", "instFamily", "instCategory", "listTime")} for i in ins if qv.get(i["instId"], 0) >= a.min},
           "qv": {k: round(qv[k], 2) for k in sorted(qv, key=lambda x: -qv[x]) if qv[k] >= a.min}}
    if a.check_candles:
        def cq(iid):
            return iid, sum(float(c[7]) for c in get(f"/market/candles?instId={iid}&bar=1H&limit=24"))
        with cf.ThreadPoolExecutor(4) as ex:
            b = dict(ex.map(cq, list(qv)))
        out["candles24h_pool"] = sorted(k for k, v in b.items() if v >= a.min)
        out["diff_vs_candles"] = {k: [round(qv[k], 2), round(b[k], 2)] for k in qv if (qv[k] >= a.min) != (b[k] >= a.min)}
    json.dump(out, open(a.out, "w"))
    print("usdt_swap_live", len(ins), "pool", len(out["pool"]), "excluded top", len(out["top_excluded"]), "diff", len(out.get("diff_vs_candles", {})))


if __name__ == "__main__":
    main()
