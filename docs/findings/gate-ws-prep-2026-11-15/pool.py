#!/usr/bin/env python3
"""G-022 Gate: пул по правилу G-022 из REST (контракты + тикеры USDT-перпетуалов).
Правило: status == trading, оборот volume_24h_quote (USDT) >= порога, топ-10 НЕ исключается (правило «без топ-10» — только Binance и Bybit), акции/индексы/металлы/форекс/сырьё входят.
usage: pool.py <out.json> [min_quote_volume=300000]"""
import json, sys, time, urllib.request

UA = {"User-Agent": "alpha-collector/gate-prep"}
META = ["quanto_multiplier", "order_size_min", "order_price_round", "mark_price_round", "enable_decimal", "contract_type", "funding_interval", "leverage_max", "is_pre_market", "in_delisting", "mark_type", "type", "launch_time"]


def get(path):
    with urllib.request.urlopen(urllib.request.Request("https://api.gateio.ws/api/v4/futures/usdt/" + path, headers=UA), timeout=30) as r:
        return json.load(r)


def main():
    out = sys.argv[1]
    min_qv = float(sys.argv[2]) if len(sys.argv) > 2 else 300000.0
    t0 = time.time()
    contracts, tickers = get("contracts"), get("tickers")
    qv_all = {t["contract"]: float(t["volume_24h_quote"]) for t in tickers}
    live = [c for c in contracts if c["status"] == "trading"]
    qv = {c["name"]: qv_all[c["name"]] for c in live if c["name"] in qv_all}
    pool = sorted(n for n, v in qv.items() if v >= min_qv)
    by = {c["name"]: c for c in live}
    meta = {n: {k: by[n].get(k) for k in META} for n in pool}
    doc = {"ts_ms": int(t0 * 1000), "min_quote_volume": min_qv, "top_excluded": [], "pool": pool, "qv": {n: round(qv[n], 2) for n in pool}, "meta": meta}
    json.dump(doc, open(out, "w"), ensure_ascii=False)
    vals = sorted((qv[n] for n in pool), reverse=True)
    kinds = {}
    for n in pool:
        k = by[n]["contract_type"] or "crypto"
        kinds[k] = kinds.get(k, 0) + 1
    print(json.dumps({"контрактов_всего": len(contracts), "status_trading": len(live), "пул": len(pool), "ниже_порога": len(qv) - len(pool), "сумма_оборота_$": round(sum(vals)), "топ10_доля": round(sum(vals[:10]) / sum(vals), 4), "медиана_$": round(vals[len(vals) // 2]), "состав": kinds, "pre_market_в_пуле": sum(1 for n in pool if by[n]["is_pre_market"]), "enable_decimal_в_пуле": sum(1 for n in pool if by[n]["enable_decimal"])}, ensure_ascii=False))


main()
