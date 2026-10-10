#!/usr/bin/env python3
"""Плечо на круг (владелец 2026-09-19: «в такие сетапы входят с плечом — выясни, на каком плече
кост-эффективно»). Плечо не меняет комиссий (они с номинала) — оно задаёт маржу на круг и расстояние до
ликвидации; кост-эффективное = максимальное, при котором ликвидация дальше стопа с запасом.

Вход:
  * publичный REST Bybit v5: `/v5/market/risk-limit` (первый ярус: ставка поддерживающей маржи MMR,
    maxLeverage, riskLimitValue), `/v5/market/funding/history` (последние N ставок);
  * JSON квантилей хода против позиции внутри дедлайна (`adverse_<H>_bps` из `lob touches`,
    сводка по пулу и по монете — `tools/compute/leverage.py --adverse adverse.json`).
Правило (не изобретённое число, а арифметика): изолированная маржа при плече L ликвидируется на
расстоянии ≈ 1/L − MMR от входа; требуем 1/L − MMR ≥ k × стоп, где k — запас на проскальзывание стопа.
Печатается L при k = 2 (запас вдвое) и L при «ликвидация дальше p99 хода против позиции внутри
дедлайна» (стоп мог не сработать — гэп); итог — минимум из них и maxLeverage монеты.

    python tools/compute/leverage.py --pool instruments.csv --adverse adverse.json \
        --stops 0.5,1,2 --deadline 3600s --out docs/findings/leverage-<дата>.csv
"""
import argparse
import csv
import json
import statistics
import time
import urllib.request

BASE = "https://api.bybit.com"


def get(path, **params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    req = urllib.request.Request(f"{BASE}{path}?{q}", headers={"User-Agent": "alpha-leverage"})
    with urllib.request.urlopen(req, timeout=20) as r:
        body = json.load(r)
    if body.get("retCode") != 0:
        raise RuntimeError(f"{path} {params}: {body.get('retMsg')}")
    return body["result"]


def pool_symbols(path):
    with open(path, encoding="utf-8") as f:
        rows = [r for r in csv.reader(f) if r and not r[0].startswith("#")]
    return [r[0] for r in rows[1:] if r[0].endswith("USDT")]


def fetch(symbols, funding_n):
    out = {}
    for i, s in enumerate(symbols):
        tiers = get("/v5/market/risk-limit", category="linear", symbol=s)["list"]
        t1 = next(t for t in tiers if str(t.get("isLowestRisk")) == "1") if tiers else None
        fh = get("/v5/market/funding/history", category="linear", symbol=s, limit=funding_n)["list"]
        rates = [abs(float(x["fundingRate"])) for x in fh]
        out[s] = {
            "mmr": float(t1["maintenanceMargin"]) if t1 else None,
            "max_lev": float(t1["maxLeverage"]) if t1 else None,
            "risk_limit_usd": float(t1["riskLimitValue"]) if t1 else None,
            "funding_abs_mean_bps": round(statistics.mean(rates) * 1e4, 3) if rates else None,
            "funding_abs_max_bps": round(max(rates) * 1e4, 3) if rates else None,
        }
        if i % 10 == 9:
            time.sleep(0.5)  # публичный лимит 10 req/s — с запасом
    return out


def leverage_for(mmr, stop_pct, adverse_p99_bps, k):
    """Плечо по двум правилам: запас k × стоп и «дальше p99 хода против» (доли, не проценты)."""
    by_stop = 1.0 / (k * stop_pct / 100.0 + mmr)
    by_adverse = 1.0 / (adverse_p99_bps / 1e4 + mmr)
    return by_stop, by_adverse


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", required=True)
    ap.add_argument("--adverse", required=True)
    ap.add_argument("--stops", default="0.5,1,2", help="стопы формы pct<x>, %")
    ap.add_argument("--deadline", default="3600s", help="ключ квантилей adverse: 60s|600s|3600s|7200s")
    ap.add_argument("--k", type=float, default=2.0, help="запас: ликвидация не ближе k × стоп")
    ap.add_argument("--funding-n", type=int, default=90, help="ставок фандинга в истории (90 = 30 дней)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--cache", help="JSON с ответами биржи (повтор без сети)")
    a = ap.parse_args()
    symbols = pool_symbols(a.pool)
    adverse = json.load(open(a.adverse, encoding="utf-8"))
    if a.cache:
        try:
            ex = json.load(open(a.cache, encoding="utf-8"))
        except FileNotFoundError:
            ex = fetch(symbols, a.funding_n)
            json.dump(ex, open(a.cache, "w", encoding="utf-8"))
    else:
        ex = fetch(symbols, a.funding_n)
    stops = [float(x) for x in a.stops.split(",")]
    pool_adv = adverse["pool"][a.deadline]["p99"]
    rows = []
    for s in symbols:
        e = ex.get(s) or {}
        if e.get("mmr") is None:
            continue
        adv = adverse["coins"].get(s, {}).get(a.deadline, {}).get("p99", pool_adv)
        n = adverse["coins"].get(s, {}).get("n", 0)
        for stop in stops:
            by_stop, by_adv = leverage_for(e["mmr"], stop, adv, a.k)
            lev = min(by_stop, by_adv, e["max_lev"])
            rows.append({
                "symbol": s, "stop_pct": stop, "deadline": a.deadline,
                "mmr_pct": round(e["mmr"] * 100, 3), "max_leverage": e["max_lev"],
                "risk_limit_usd": e["risk_limit_usd"],
                "adverse_p99_bps": adv, "adverse_n": n, "adverse_source": "coin" if n else "pool",
                "lev_by_stop_k": round(by_stop, 1), "lev_by_adverse_p99": round(by_adv, 1),
                "leverage": int(lev), "margin_per_10k_usd": round(10_000 / int(lev), 1) if int(lev) else None,
                "liq_distance_pct": round((1 / int(lev) - e["mmr"]) * 100, 2) if int(lev) else None,
                "funding_abs_mean_bps_8h": e["funding_abs_mean_bps"],
                "funding_abs_max_bps_8h": e["funding_abs_max_bps"],
            })
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    # Сводка в консоль: по стопу — медиана и минимум плеча по пулу.
    for stop in stops:
        levs = [r["leverage"] for r in rows if r["stop_pct"] == stop]
        print(f"stop {stop}%: плечо по пулу медиана {statistics.median(levs):.0f}, "
              f"min {min(levs)}, max {max(levs)}, монет {len(levs)}")
    mm = [r["mmr_pct"] for r in rows if r["stop_pct"] == stops[0]]
    fr = [r["funding_abs_mean_bps_8h"] for r in rows if r["stop_pct"] == stops[0] and r["funding_abs_mean_bps_8h"] is not None]
    print(f"MMR первого яруса: медиана {statistics.median(mm):.2f}%, max {max(mm):.2f}%; "
          f"|фандинг| за 8 ч: медиана {statistics.median(fr):.2f} bps, max {max(fr):.2f} bps")
    print(f"-> {a.out}")


if __name__ == "__main__":
    main()
