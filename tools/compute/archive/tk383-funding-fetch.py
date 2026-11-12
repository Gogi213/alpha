#!/usr/bin/env python3
"""TK-383: история фандинга Bybit/Binance/Gate/Bitget/BingX с 2026-09-01 UTC -> data/tk383/funding.csv
(ex,coin,sym,t_ms,rate,interval_h). Публичные API, без ключей."""
import csv, re, sys, time, json, os
from concurrent.futures import ThreadPoolExecutor
import requests

START = int(time.mktime(time.strptime("2026-09-01", "%Y-%m-%d"))) * 1000  # локальное=UTC+4 -> поправка ниже
START -= 4 * 3600 * 1000  # 2026-09-01 00:00 UTC при локальной зоне GMT+4
NOW = int(time.time() * 1000)
OUT = sys.argv[1] if len(sys.argv) > 1 else "data/tk383"
os.makedirs(OUT, exist_ok=True)
S = requests.Session()


def get(url, params=None, tries=4):
    for i in range(tries):
        try:
            r = S.get(url, params=params, timeout=20)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 418):
                time.sleep(2 + 3 * i)
        except Exception:
            time.sleep(1 + i)
    return None


def coin_of(base):
    b = base.upper()
    b = re.sub(r"^(1000000|10000|1000|1M)", "", b)
    return b


def lists():
    L = {}
    j = get("https://api.bybit.com/v5/market/instruments-info", {"category": "linear", "limit": 1000})
    L["bybit"] = {i["symbol"]: coin_of(i["baseCoin"]) for i in j["result"]["list"]
                  if i["quoteCoin"] == "USDT" and i["contractType"] == "LinearPerpetual" and i["status"] == "Trading"}
    j = get("https://fapi.binance.com/fapi/v1/exchangeInfo")
    L["binance"] = {i["symbol"]: coin_of(i["baseAsset"]) for i in j["symbols"]
                    if i["quoteAsset"] == "USDT" and i["contractType"] == "PERPETUAL" and i["status"] == "TRADING"}
    j = get("https://api.gateio.ws/api/v4/futures/usdt/contracts")
    L["gate"] = {i["name"]: coin_of(i["name"].split("_")[0]) for i in j if not i.get("in_delisting")}
    j = get("https://api.bitget.com/api/v2/mix/market/contracts", {"productType": "USDT-FUTURES"})
    L["bitget"] = {i["symbol"]: coin_of(i["baseCoin"]) for i in j["data"] if i.get("symbolStatus") == "normal"}
    j = get("https://open-api.bingx.com/openApi/swap/v2/quote/contracts")
    L["bingx"] = {i["symbol"]: coin_of(i["symbol"].split("-")[0]) for i in j["data"]
                  if i.get("status") == 1 and i["symbol"].endswith("-USDT")}
    return L


def hist(ex, sym):
    rows = []  # (t_ms, rate)
    if ex == "bybit":
        end = NOW
        while True:
            j = get("https://api.bybit.com/v5/market/funding/history",
                    {"category": "linear", "symbol": sym, "startTime": START, "endTime": end, "limit": 200})
            if not j or j["retCode"] != 0:
                break
            l = j["result"]["list"]
            if not l:
                break
            rows += [(int(x["fundingRateTimestamp"]), float(x["fundingRate"])) for x in l]
            old = min(int(x["fundingRateTimestamp"]) for x in l)
            if len(l) < 200 or old <= START:
                break
            end = old - 1
    elif ex == "binance":
        st = START
        while True:
            j = get("https://fapi.binance.com/fapi/v1/fundingRate", {"symbol": sym, "startTime": st, "limit": 1000})
            if not j:
                break
            rows += [(int(x["fundingTime"]), float(x["fundingRate"])) for x in j]
            if len(j) < 1000:
                break
            st = int(j[-1]["fundingTime"]) + 1
    elif ex == "gate":
        j = get("https://api.gateio.ws/api/v4/futures/usdt/funding_rate",
                {"contract": sym, "from": START // 1000, "to": NOW // 1000, "limit": 1000})
        if j:
            rows += [(int(x["t"]) * 1000, float(x["r"])) for x in j]
    elif ex == "bitget":
        pg = 1
        while pg < 30:
            j = get("https://api.bitget.com/api/v2/mix/market/history-fund-rate",
                    {"symbol": sym, "productType": "USDT-FUTURES", "pageSize": 100, "pageNo": pg})
            if not j or j.get("code") != "00000" or not j["data"]:
                break
            l = j["data"]
            rows += [(int(x["fundingTime"]), float(x["fundingRate"])) for x in l]
            if len(l) < 100 or min(int(x["fundingTime"]) for x in l) <= START:
                break
            pg += 1
    elif ex == "bingx":
        end = NOW
        while True:
            j = get("https://open-api.bingx.com/openApi/swap/v2/quote/fundingRate",
                    {"symbol": sym, "startTime": START, "endTime": end, "limit": 1000})
            if not j or j.get("code") != 0 or not j["data"]:
                break
            l = j["data"]
            rows += [(int(x["fundingTime"]), float(x["fundingRate"])) for x in l]
            old = min(int(x["fundingTime"]) for x in l)
            if len(l) < 1000 or old <= START:
                break
            end = old - 1
    rows = sorted({t: r for t, r in rows if START <= t <= NOW}.items())
    return rows


def main():
    L = lists()
    json.dump(L, open(f"{OUT}/lists.json", "w"))
    cnt = {}
    for ex, d in L.items():
        for c in d.values():
            cnt.setdefault(c, set()).add(ex)
    multi = {c for c, e in cnt.items() if len(e) >= 2}
    print({ex: len(d) for ex, d in L.items()}, "coins>=2 ex:", len(multi), flush=True)
    jobs = [(ex, s, c) for ex, d in L.items() for s, c in d.items() if c in multi]
    print("jobs", len(jobs), flush=True)

    def run(job):
        ex, s, c = job
        return ex, c, s, hist(ex, s)

    n = 0
    with open(f"{OUT}/funding.csv", "w", newline="") as f, ThreadPoolExecutor(20) as pool:
        w = csv.writer(f)
        w.writerow(["ex", "coin", "sym", "t_ms", "rate"])
        for ex, c, s, rows in pool.map(run, jobs):
            for t, r in rows:
                w.writerow([ex, c, s, t, r])
            n += 1
            if n % 200 == 0:
                print(n, flush=True)
    print("done", n)


main()
