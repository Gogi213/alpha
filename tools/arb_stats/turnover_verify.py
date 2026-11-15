"""Проверка оборота на странице против 24-часовых тикеров бирж (после выкладки; запускается на srv — там открыт доступ к API бирж).

python3 turnover_verify.py <url /api/arb> [монеты через запятую; по умолчанию BTC,SOL,ZORA] [допуск, по умолчанию 0.10]
Берёт строки /api/arb (v = [$ первой биржи, $ второй] за окно vwin), собирает оборот монеты по биржам и сравнивает с тикером биржи (Binance/BingX — quoteVolume,
Bybit — turnover24h, Gate — volume_24h_quote; OKX отдаёт объём в монете, а не в $ — в таблице «—»). Окна тикера и страницы не совпадают на минуты, поэтому допуск ±10 % по умолчанию.
Код выхода 0 — у всех проверяемых (монета, биржа) отношение в допуске и у ≥ 90 % строк есть v.
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import norm  # noqa: E402
from turnover_check import tick  # noqa: E402

URLS = {
    "binance": "https://fapi.binance.com/fapi/v1/ticker/24hr",
    "bybit": "https://api.bybit.com/v5/market/tickers?category=linear",
    "okx": "https://www.okx.com/api/v5/market/tickers?instType=SWAP",
    "bingx": "https://open-api.bingx.com/openApi/swap/v2/quote/ticker",
    "gate": "https://fx-api.gateio.ws/api/v4/futures/usdt/tickers",
}


def fetch_tickers():
    out = {}
    for ex, u in URLS.items():
        t = time.time()
        body = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=30).read()
        out[ex] = {"t_req": int(t * 1000), "t_resp": int(time.time() * 1000), "body": json.loads(body)}
    return out


def main(url, coins, tol):
    doc = json.loads(urllib.request.urlopen(url, timeout=30).read())
    rows = doc["rows"]
    withv = sum(1 for r in rows if r.get("v"))
    print(f"строк {len(rows)}, с оборотом {withv} ({withv / max(1, len(rows)):.1%}), окно оборота vwin = {doc.get('vwin')}")
    ours = {}
    for r in rows:
        if r["c"] in coins and r.get("v"):
            ours.setdefault(r["c"], {})[r["a"]] = r["v"][0]
            ours[r["c"]][r["b"]] = r["v"][1]
    tk = fetch_tickers()
    theirs = {}
    for ex in URLS:
        for s, v in tick(ex, tk).items():
            c = norm(ex, s, {s: {"displayName": s}})[0]
            if c in coins and v[2] is not None:
                theirs.setdefault(c, {})[ex] = v[2]
    ok = withv >= 0.9 * len(rows)
    print("монета биржа | страница $ | тикер $ | страница / тикер")
    for c in sorted(coins):
        for ex in URLS:
            a, t = ours.get(c, {}).get(ex), theirs.get(c, {}).get(ex)
            if a is None:
                print(f"{c:6s} {ex:8s} | нет на странице")      # монеты нет на этой бирже (или пары нет в таблице)
                continue
            if t is None:
                print(f"{c:6s} {ex:8s} | {a:16,.0f} | {'—':>16} | —")
                continue
            r = a / t
            ok = ok and abs(r - 1) <= tol
            print(f"{c:6s} {ex:8s} | {a:16,.0f} | {t:16,.0f} | {r:.4f}")
    print("ИТОГ:", "в допуске" if ok else "ВНЕ ДОПУСКА")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], set((sys.argv[2] if len(sys.argv) > 2 else "BTC,SOL,ZORA").split(",")), float(sys.argv[3]) if len(sys.argv) > 3 else 0.10))
