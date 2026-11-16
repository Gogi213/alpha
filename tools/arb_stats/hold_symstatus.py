"""Класс и статус символов пяти бирж для фильтров arb-holding: акции/ETF/сырьё/форекс, делистинг, пред-листинг. Снимок на момент запуска (REST Binance, Bybit; OKX, BingX, Gate — из пулов сборщиков).

Запуск: python3 hold_symstatus.py <выход.json>   -> {биржа: {символ: {"kind", "status", "delist_ms", "launch_ms", "pre"}}}
kind: crypto | equity | etf | commodity | forex | index. delist_ms — время поставки/делистинга, если биржа его дала (иначе None).
"""
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from hold_common import EX5, all_meta  # noqa: E402

FAR = 4_000_000_000_000


def get(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read())


def main(out):
    res = {ex: {} for ex in EX5}
    for x in get("https://fapi.binance.com/fapi/v1/exchangeInfo")["symbols"]:
        kind = {"EQUITY": "equity", "COMMODITY": "commodity", "INDEX": "index", "FOREX": "forex"}.get(x.get("underlyingType"), "crypto")
        if x.get("contractType") == "TRADIFI_PERPETUAL" and kind == "crypto":
            kind = "equity"
        d = int(x["deliveryDate"])
        res["binance"][x["symbol"]] = {"kind": kind, "status": x["status"], "delist_ms": d if d < FAR else None, "launch_ms": int(x["onboardDate"]), "pre": x["status"] == "PENDING_TRADING"}
    cursor = ""
    while True:
        d = get("https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1000" + (f"&cursor={cursor}" if cursor else ""))["result"]
        for x in d["list"]:
            kind = {"stock": "equity", "ETF": "etf", "commodity": "commodity", "forex": "forex"}.get(x.get("symbolType"), "crypto")
            dt = int(x.get("deliveryTime") or 0)
            res["bybit"][x["symbol"]] = {"kind": kind, "status": x["status"], "delist_ms": dt or None, "launch_ms": int(x.get("launchTime") or 0), "pre": bool(x.get("isPreListing"))}
        cursor = d.get("nextPageCursor") or ""
        if not cursor:
            break
    days = ["2026-11-13", "2026-11-14", "2026-11-15", "2026-11-16"]
    okx_kind = {"1": "crypto", "3": "equity", "4": "commodity", "5": "forex", "6": "index"}
    for s, m in all_meta("okx", days).items():
        res["okx"][s] = {"kind": okx_kind.get(m.get("instCategory"), "crypto"), "status": "live", "delist_ms": None, "launch_ms": int(m.get("listTime") or 0), "pre": False}
    for s, m in all_meta("bingx", days).items():
        ok = m.get("apiStateOpen") == "true" and m.get("apiStateClose") == "true"
        res["bingx"][s] = {"kind": "crypto", "status": "live" if ok else "api_closed", "delist_ms": None, "launch_ms": int(m.get("launchTime") or 0), "pre": False}
    for s, m in all_meta("gate", days).items():
        ct = m.get("contract_type") or ""
        kind = {"stocks": "equity", "etf": "etf", "commodities": "commodity", "forex": "forex", "indices": "index"}.get(ct, "crypto")
        res["gate"][s] = {"kind": kind, "status": "delisting" if m.get("in_delisting") else "live", "delist_ms": None, "launch_ms": int(m.get("launch_time") or 0) * 1000, "pre": bool(m.get("is_pre_market"))}
    Path(out).write_text(json.dumps(res), encoding="utf-8")
    for ex, d in res.items():
        k = {}
        for v in d.values():
            k[v["kind"]] = k.get(v["kind"], 0) + 1
        print(ex, len(d), k, "settling/delisting:", sum(v["status"] in ("SETTLING", "delisting") for v in d.values()))


if __name__ == "__main__":
    main(sys.argv[1])
