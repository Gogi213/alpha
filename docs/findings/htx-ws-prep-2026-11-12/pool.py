#!/usr/bin/env python3
"""Пул HTX (Huobi) по правилу G-022: USDT-маржинальные бессрочные (linear swap, business_type=swap, trade_partition=USDT), contract_status=1 (торгуется), оборот за 24 ч ≥ порога $, топ-10 не исключается.
Оборот — `trade_turnover` из REST /linear-swap-ex/market/detail/batch_merged (документация: «sum (transaction quantity * contract face value * transaction price), from nowtime - 24 hours», котируемая валюта USDT).
Метаданные — /linear-swap-api/v1/swap_contract_info. Хост: api.hbdm.com.
  pool.py --out pool-DAY.json [--min 300000]"""
import argparse, json, time, urllib.request

HOST = "https://api.hbdm.com"


def get(path):
    for k in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(HOST + path, headers={"User-Agent": "alpha-collector/htx-pool"}), timeout=30) as r:
                d = json.load(r)
                assert d["status"] == "ok", d
                return d
        except Exception:
            time.sleep(1 + k)
    raise RuntimeError(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--min", type=float, default=300_000.0)
    a = ap.parse_args()
    t0 = time.time()
    ins = [i for i in get("/linear-swap-api/v1/swap_contract_info")["data"] if i["business_type"] == "swap" and i["contract_type"] == "swap" and i["trade_partition"] == "USDT" and i["contract_status"] == 1]
    tk = {t["contract_code"]: t for t in get("/linear-swap-ex/market/detail/batch_merged?business_type=swap")["ticks"]}
    qv = {i["contract_code"]: float(tk[i["contract_code"]]["trade_turnover"]) for i in ins if i["contract_code"] in tk}
    out = {"ts_ms": int(t0 * 1000), "source": "htx REST /linear-swap-api/v1/swap_contract_info + /linear-swap-ex/market/detail/batch_merged (business_type=swap): contract_type=swap, trade_partition=USDT, contract_status=1; turnover=trade_turnover (24 ч, USDT)",
           "min_quote_volume": a.min, "top_excluded": [], "n_usdt_swap_trading": len(ins), "pool": sorted(k for k, v in qv.items() if v >= a.min),
           "meta": {i["contract_code"]: {k: i[k] for k in ("symbol", "contract_size", "price_tick", "create_date", "settlement_period", "labels", "tradfi_labels", "enable_rpi")} for i in ins if qv.get(i["contract_code"], 0) >= a.min},
           "qv": {k: round(qv[k], 2) for k in sorted(qv, key=lambda x: -qv[x]) if qv[k] >= a.min}}
    json.dump(out, open(a.out, "w"))
    print("usdt_swap_trading", len(ins), "pool", len(out["pool"]))


if __name__ == "__main__":
    main()
